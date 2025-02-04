import logging
from datetime import datetime
from typing import List, Dict


logger = logging.getLogger(__name__)


class ChatHistory:
    def __init__(self, max_turns_for_prompt: int = 10):
        self.conversation_turns = []
        self.max_turns_for_prompt = max_turns_for_prompt

    def add_turn(self, role: str, content: str) -> None:
        """Add a turn to conversation history with full metadata for MongoDB"""
        self.conversation_turns.append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        })

    def format_history_for_prompt(self) -> str:
        """Format last N turns in simple format for prompt to save tokens"""
        recent_turns = self.conversation_turns[-self.max_turns_for_prompt:]
        return "\n".join(
            f"{turn['role'].capitalize()}: {turn['content']}"
            for turn in recent_turns
        )

    def get_full_history(self) -> List[Dict]:
        """Get full conversation history for MongoDB storage"""
        return self.conversation_turns

    def process_msg_history(self, message_history: List[Dict]) -> None:
        """Process and add message history to conversation turns"""
        if not message_history:
            return
            
        for msg in message_history:
            if hasattr(msg, 'role') and msg.role == 'user':
                # Extract user message
                actual_query = msg.content.split("Current query: ")[-1].split("\n")[0]
                if not any(turn['content'] == actual_query and turn['role'] == 'user' 
                           for turn in self.conversation_turns):
                    self.add_turn('user', actual_query)
            elif hasattr(msg, 'parts'):
                # Handle response messages
                response_content = next(
                    (part.content for part in msg.parts if part.part_kind == 'text'),
                    None
                )
                if response_content and not any(turn['content'] == response_content and turn['role'] == 'assistant'
                                            for turn in self.conversation_turns):
                    self.add_turn('assistant', response_content)