"""python -m src.backend.chat_main"""
import logging
import logfire
import hydra
import asyncio
from omegaconf import DictConfig
import asyncio

from src.backend.utils.logging import setup_logging
from src.backend.chat.query_handler import QueryHandler

logger = logging.getLogger(__name__)
logfire.configure(send_to_logfire='if-token-present')


class CLITester:
    def __init__(self, cfg: DictConfig):
        self.cfg = cfg
        self.query_handler = QueryHandler(cfg.csv_path, cfg)
        self.message_history = []

    def print_conversation(self, role: str, content: str) -> None:
        print(f"\n{role.capitalize()}: {content}")

    async def process_query(self, query: str) -> None:
        self.print_conversation("user", query)
        response = await self.query_handler.handle_query(query)
        self.print_conversation("assistant", response)

    async def run(self) -> None:
        """Run the CLI tester"""
        print("\nWelcome to the Edu Chatbot Tester!")
        print("Type 'quit' or 'exit' to end the session")
        print("Type 'clear' to clear conversation history")
        print("Type 'history' to see conversation history\n")

        while True:
            try:
                query = input("\nEnter your query: ").strip()

                if query.lower() in ['quit', 'exit']:
                    print("Goodbye!")
                    break
                
                elif query.lower() == 'clear':
                    self.conversation_history = []
                    print("Conversation history cleared!")
                    continue
                
                elif query.lower() == 'history':
                    print("\nConversation History:")
                    for msg in self.conversation_history:
                        print(f"{msg['role']}: {msg['content']}")
                    continue

                elif not query:
                    continue

                # Process the query
                await self.process_query(query)

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                logging.error(f"Error processing query: {e}")
                print(f"Error: {e}")


@hydra.main(
    version_base=None,
    config_path="../../config",
    config_name="chat")
def main(cfg) -> None:
    logger = logging.getLogger(__name__)
    logger.info("Setting up logging configuration.")
    setup_logging()

    async def async_main():
        tester = CLITester(cfg)
        await tester.run()
    
    asyncio.run(async_main())


if __name__ == "__main__":
    main()