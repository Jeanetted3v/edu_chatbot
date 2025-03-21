from typing import Dict, Union, List
import os
import logging
import pypdf
import pandas as pd
from omegaconf import DictConfig
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadedUnstructuredDocument:
    """Represents a loaded document with its content and metadata."""
    content: str
    metadata: Dict[str, str]


@dataclass
class LoadedStructuredDocument:
    """Loaded structured document (CSV, Excel) with content & metadata."""
    content: pd.DataFrame
    metadata: Dict[str, str]


class LocalDocLoader:
    """Loads documents of various formats (PDF) into unified format."""
    @staticmethod
    def convert_excel_to_csv(excel_path: str, sheet_name: str = None) -> str:
        """
        Convert Excel file to CSV and save in the same directory
        
        Args:
            excel_path: Path to the Excel file
            
        Returns:
            Path to the saved CSV file
        """
        try:
            directory = os.path.dirname(excel_path)
            parent_directory = os.path.dirname(directory)
            base_name = os.path.splitext(os.path.basename(excel_path))[0]
            sheet_suffix = f"_{sheet_name}" if sheet_name else ""
            csv_path = os.path.join(
                parent_directory, f"{base_name}{sheet_suffix}.csv")
            
            df = pd.read_excel(excel_path, sheet_name=sheet_name)
            df.to_csv(csv_path, index=False, encoding='utf-8-sig')
            
            logger.info(f"Successfully converted Excel to CSV: {csv_path}")
            return csv_path
            
        except Exception as e:
            logger.error(f"Error converting Excel to CSV: {str(e)}")
            raise
    
    def _load_pdf(self, file_path: str) -> LoadedUnstructuredDocument:
        """Load a PDF document."""
        metadata = {
            'source': file_path,
            'type': 'pdf'
        }
        try: 
            with open(file_path, 'rb') as file:
                pdf = pypdf.PdfReader(file)
                if len(pdf.pages) == 0:
                    raise ValueError(f"PDF file {file_path} is empty")
                content = []
                total_chars = 0
                
                for i, page in enumerate(pdf.pages, 1):
                    try: 
                        text = page.extract_text()
                        if text:
                            content.append(text)
                            chars_in_page = len(text)
                            total_chars += chars_in_page
                            metadata[f'page_{i}_length'] = str(chars_in_page)
                
                    except Exception as e:
                        logger.warning("Error reading page {i} of "
                                       f" PDF {file_path}: {str(e)}")
                metadata['total_pages'] = str(len(pdf.pages))
                
                full_text = "\n\n".join(content)
                
                if not full_text.strip():
                    raise ValueError("No text could be extracted from "
                                     f"PDF {file_path}")
                doc = LoadedUnstructuredDocument(
                    content=full_text, metadata=metadata)
                return doc  
        except FileNotFoundError:
            raise FileNotFoundError(f"PDF file not found: {file_path}")
        except Exception as e:
            raise ValueError(f"Error reading PDF {file_path}: {str(e)}")
    
    def _load_document(self, cfg: DictConfig) -> Union[
        LoadedUnstructuredDocument,
        LoadedStructuredDocument
    ]:
        """
        Load a document and return its contents with metadata.
        
        Args:
            file_path: Path to the document file
            
        Returns:
            LoadedDocument object containing text content and metadata
        
        Raises:
            ValueError: If file format is not supported
            FileNotFoundError: If file doesn't exist
        """
        file_path = cfg.path
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        file_ext = os.path.splitext(file_path)[1].lower()
        
        if file_ext == '.pdf':
            return self._load_pdf(file_path)
        elif file_ext in ['.xlsx', '.xls']:
            if not hasattr(cfg, 'sheet'):
                raise ValueError("Sheet name is required for Excel file")
            self.convert_excel_to_csv(file_path, cfg.sheet)
        else:
            raise ValueError(f"Unsupported file format: {file_ext}")


def load_local_doc(
    cfg: DictConfig
) -> List[Union[LoadedUnstructuredDocument, LoadedStructuredDocument]]:
    """
    Load documents from local filesystem based on configuration.
    
    Args:
        cfg: Hydra configuration object
        doc_loader: Instance of DocLoader
        
    Returns:
        List of loaded documents
    """
    documents = []
    
    # Load documents from configured paths
    for cfg in cfg.local_doc.paths:
        try:
            doc_loader = LocalDocLoader()
            doc = doc_loader._load_document(cfg)
            documents.append(doc)
            logger.info(f"Successfully loaded document: {cfg['path']}")
            
            # Log preview and metadata
            if isinstance(doc, LoadedUnstructuredDocument):
                logger.debug(f"Content preview: {doc.content[:200]}...")
                logger.debug(f"Metadata: {doc.metadata}")
            
        except Exception as e:
            logger.error(f"Error loading document {cfg['path']}: {str(e)}")
            
    return documents
