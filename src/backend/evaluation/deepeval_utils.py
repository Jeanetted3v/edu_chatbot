"""Utility class for DeepEval test preparation."""

import os
import json, csv
import pandas as pd
import logging
from omegaconf import DictConfig
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime
from pydantic import BaseModel
from pydantic_ai import Agent

from deepeval.dataset import EvaluationDataset
from deepeval.test_case import ConversationalTestCase, LLMTestCase
from src.backend.dataloaders.local_doc_loader import (
    load_local_doc,
    LoadedUnstructuredDocument,
    LoadedStructuredDocument
)

logger = logging.getLogger(__name__)

class LLMGroundTruth(BaseModel):
    customer_inquiry: str
    llm_gt: str

class AllLLMGroundTruth(BaseModel):
    all_llmgt: List[LLMGroundTruth]


class EvalUtils:
    """Utility class for preparing DeepEval test cases from MongoDB data."""
    
    def __init__(self, chat_history_collection=None):
        """Initialize the utility class."""
        self.chat_history_collection = chat_history_collection

    async def extract_conversations_by_session(
        self, session_id: str, limit: int = 100
    ) -> List[Dict]:
        """Extract conversation history for a specific session.
        
        Args:
            session_id: The unique session identifier
            limit: Maximum number of messages to retrieve
            
        Returns:
            List of message dictionaries sorted by timestamp
        """
        if self.chat_history_collection is None:
            await self.connect()
            
        cursor = self.chat_history_collection.find(
            {"session_id": session_id}
        ).sort("timestamp", 1).limit(limit)
        return await cursor.to_list(length=limit)
    
    async def extract_conversations_by_customer(
        self, customer_id: str, limit: int = 100
    ) -> Dict[str, List[Dict]]:
        msg_cursor = self.chat_history_collection.find(
            {"customer_id": customer_id}
        ).sort("timestamp", 1).limit(limit)
        all_msg = await msg_cursor.to_list(length=limit)
        return all_msg

    async def export_convo_to_csv(
        self,
        session_ids: List[str],
        output_file: str,
        session_chat_limit: int = 100
    ) -> None:
        try:
            with open(output_file, 'w', newline='', encoding='utf-8') as csvf:
                fieldnames = [
                    'session_id', 'customer_inquiry',
                    'bot_response', 'retrieval_context', 'context',
                    'expected_output'
                ]
                writer = csv.DictWriter(csvf, fieldnames=fieldnames)
                writer.writeheader()

                total_rows = 0
                for session_id in session_ids:
                    try:
                        messages = await self.extract_conversations_by_session(
                            session_id, limit=session_chat_limit
                        )
                        messages.sort(key=lambda x: x.get("timestamp", 0))
                        i = 0
                        while i < len(messages) - 1:
                            current_msg = messages[i]
                            next_msg = messages[i + 1]
                            if (current_msg['role'] == 'user' and
                                    next_msg['role'] == 'bot'):
                                retrieval_context = ""
                                if "metadata" in next_msg:
                                    metadata = next_msg["metadata"]
                                    logger.info(f"Metadata: {metadata}")
                                    if metadata:
                                        retrieval_context = metadata.get("top_search_result", "")
                                        logger.info(f"Retrieval Context: {retrieval_context}")
                                row = {
                                    'session_id': session_id,
                                    'customer_inquiry': current_msg.get("content", ""),
                                    'bot_response': next_msg.get("content", ""),
                                    'retrieval_context': retrieval_context,
                                    'context': "",
                                    'expected_output': ""
                                }
                                writer.writerow(row)
                                total_rows += 1
                                i += 2  # skip to the next pair
                            else:
                                i += 1
                    except Exception as e:
                        logger.error(f"Error processing session {session_id}: {e}")
                logger.info(f"Successfully exported {total_rows} "
                            f"conversation pairs to {output_file}")
        except Exception as e:
            logger.error(f"Error exporting conversations to CSV: {e}")
            raise
    
    async def fill_gt_llm(
        self,
        cfg: DictConfig,
        csv_path: str,
        system_prompt: str,
        user_prompt: str
    ) -> None:
        documents = load_local_doc(cfg)
        rag_context = ""
        for doc in documents:
            if isinstance(doc, LoadedUnstructuredDocument):
                rag_context += f"\n\n{doc.content}"
            elif isinstance(doc, LoadedStructuredDocument):
                df_str = doc.content.to_string(index=False)
                rag_context += f"\n\n{df_str}"

        df = pd.read_csv(csv_path)
        if "customer_inquiry" not in df.columns:
            raise ValueError("CSV does not contain 'customer_inquiry' column")
        if "expected_output" not in df.columns:
            df["expected_output"] = ""
        inquiries = df["customer_inquiry"].tolist()

        agent = Agent(
            "openai:gpt-4o-mini",
            result_type=AllLLMGroundTruth,
            system_prompt=system_prompt
        )
        formatted_inquiries = "\n".join([f"Inquiry {i+1}: {inq}" 
                                        for i, inq in enumerate(inquiries)])
        result = await agent.run(user_prompt.format(
            customer_inquiry=formatted_inquiries,
            context=rag_context,
            )
        )
        all_llmgt = result.data.all_llmgt
        print(f"LLM Ground Truth: {all_llmgt}")
        for i, item in enumerate(all_llmgt):
            if i < len(df):
                df.at[i, "expected_output"] = item.llm_gt

        df.to_csv(csv_path, index=False)
        logger.info(f"Updated CSV with LLM ground truth: {csv_path}")
        return None
    
    async def load_dataset_from_csv(
        self, csv_file_path: str, chatbot_role: str
    ) -> EvaluationDataset:
        dataset = EvaluationDataset()
        dataset.add_test_cases_from_csv_file(
            file_path=csv_file_path,
            input_col_name="customer_inquiry",
            actual_output_col_name="bot_response",
            context_col_name="context",
            context_col_delimiter=";",
            expected_output_col_name="expected_output",
            retrieval_context_col_name="retrieval_context",
            retrieval_context_col_delimiter=";"
        )
        print(f"Dataset: {dataset}")
        if dataset.test_cases:
            conv_test_case = ConversationalTestCase(
                chatbot_role=chatbot_role,
                turns=dataset.test_cases
            )
            return EvaluationDataset(test_cases=[conv_test_case])
        return EvaluationDataset()

    async def save_results_deepeval(
        self,
        session_id: List[str],
        metrics_results: List[Tuple[str, float, str, bool]],
        base_dir: str,
    ) -> dict:
        """Save evaluation results to a CSV and a JSON file.
        
        Args:
            result: Result object from assert_test
            base_dir: Base directory for saving results
            
        Returns:
            Path to the saved file
        """
        os.makedirs(base_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"deepeval_{session_id}_{timestamp}.json"
        json_filepath = os.path.join(base_dir, filename)
        csv_filename = f"deepeval_{session_id}_{timestamp}.csv"
        csv_filepath = os.path.join(base_dir, csv_filename)

        result_data = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "metrics": {},
        }
        for metric_name, score, reason, passed in metrics_results:
            result_data["metrics"][metric_name] = {
                "score": score,
                "passed": passed,
                "reason": reason
            }
    
        with open(csv_filepath, 'w', newline='') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow([
                'Session ID', 'Timestamp', 'Metric', 'Score', 'Passed',
                'Reason',
            ])
        
            # Write rows for each metric
            for metric_name, score, reason, passed in metrics_results:
                csv_writer.writerow([
                    session_id,
                    datetime.now().isoformat(),
                    metric_name,
                    score,
                    'PASSED' if passed else 'FAILED',
                    reason[:500]
                ])
        with open(json_filepath, 'w') as json_file:
            json.dump(result_data, json_file, indent=4)
        
        return {
            "json_path": json_filepath,
            "csv_path": csv_filepath
        }
    
