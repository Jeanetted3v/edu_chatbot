# src/backend/api/websocket_router.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from datetime import datetime
import json
from starlette.websockets import WebSocketState
from src.backend.api.deps import get_websocket_service_container
from src.backend.chat.service_container import ServiceContainer
from src.backend.websocket.manager import manager
from src.backend.api.utils_router import human_takeover
from src.backend.models.human_agent import ToggleReason
from src.backend.models.api import MessageRole, AgentType
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


async def handle_customer_message(
    services, session_id, customer_id, content, manager
):
    """Process a message from a customer via WebSocket
    
    Get a response, then the broadcast_to_session function takes that message
    or response, forwards it to every client (both customer and staff
    interfaces) that's connected to the same chat session
    """
    response = await services.query_handler.handle_query(
        content,
        session_id,
        customer_id
    )
    session = await services.get_or_create_session(session_id, customer_id)
    response_role = (
        MessageRole.HUMAN_AGENT 
        if session.current_agent == AgentType.HUMAN 
        else MessageRole.BOT
    )
    # Broadcast the customer's message to all connections
    await manager.broadcast_to_session(
        session_id,
        {
            "type": "new_message",
            "message": {
                "role": MessageRole.USER,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
                "customer_id": customer_id
            }
        }
    )
    # Broadcast the response, from bot or human agent, to all connections
    await manager.broadcast_to_session(
        session_id,
        {
            "type": "new_message",
            "message": {
                "role": response_role,
                "content": response,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
                "customer_id": customer_id
            }
        }
    )


async def handle_staff_message(
    services, session_id, customer_id, content, manager
):
    """Process a message from a staff member via WebSocket"""
    session = await services.get_or_create_session(session_id, customer_id)
    chat_history = await services.get_chat_history(session_id, customer_id)
    
    # Ensure session is in human agent mode
    if session.current_agent != AgentType.HUMAN:
        await human_takeover(
            session_id=session_id,
            reason=ToggleReason.AGENT_INITIATED,
            services=services
        )
    
    await chat_history.add_turn(MessageRole.HUMAN_AGENT, content)
    session.last_interaction = datetime.now()
    
    # Broadcast the staff message to all connections
    await manager.broadcast_to_session(
        session_id,
        {
            "type": "new_message",
            "message": {
                "role": MessageRole.HUMAN_AGENT,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "session_id": session_id,
                "customer_id": customer_id
            }
        }
    )


async def handle_command(
    services, session_id, customer_id, action, manager
):
    """Process a command from a staff member via WebSocket"""
    try:
        session = await services.get_or_create_session(session_id, customer_id)
        chat_history = await services.get_chat_history(session_id, customer_id)
        
        if action == "takeover":
            # Only proceed if not already in human mode
            if session.current_agent == AgentType.HUMAN:
                await manager.send_command_message(
                    {
                        "type": "command_result",
                        "action": "takeover",
                        "success": False,
                        "message": "Session already handled by human agent"
                    },
                    session_id
                )
                return
            
            # Use the existing takeover function
            takeover_message = await human_takeover(
                session_id=session_id,
                reason=ToggleReason.AGENT_INITIATED,
                services=services
            )
            
            # Notify the client that the takeover was successful
            await manager.send_command_message(
                {
                    "type": "command_result",
                    "action": "takeover",
                    "success": True,
                    "message": takeover_message
                },
                session_id
            )
            
        elif action == "transfer_to_bot":
            # Only proceed if currently in human mode
            if session.current_agent != AgentType.HUMAN:
                await manager.send_command_message(
                    {
                        "type": "command_result",
                        "action": "transfer_to_bot",
                        "success": False,
                        "message": "Session already handled by bot"
                    },
                    session_id
                )
                return
            
            # Transfer to bot
            transfer_message = await services.human_handler.transfer_to_bot(
                session_id,
                chat_history
            )
            
            # Update session agent type
            session.current_agent = AgentType.BOT
            
            # Notify the client that the transfer was successful
            await manager.send_command_message(
                {
                    "type": "command_result",
                    "action": "transfer_to_bot",
                    "success": True,
                    "message": transfer_message
                },
                session_id
            )
            await manager.broadcast_to_session(
                session_id,
                {
                    "type": "new_message",
                    "message": {
                        "role": "SYSTEM",
                        "content": transfer_message,
                        "timestamp": datetime.now().isoformat(),
                        "session_id": session_id,
                        "customer_id": customer_id
                    }
                }
            )
        else:
            # Unknown action
            await manager.send_command_message(
                {
                    "type": "command_result",
                    "action": action,
                    "success": False,
                    "message": f"Unknown action: {action}"
                },
                session_id
            )
    except Exception as e:
        logger.error(f"Error handling command {action}: {e}")
        await manager.send_command_message(
            {
                "type": "command_result",
                "action": action,
                "success": False,
                "message": f"Error: {str(e)}"
            },
            session_id
        )



@router.websocket("/chat/{session_id}/{client_type}")
async def websocket_endpoint(
    websocket: WebSocket, 
    session_id: str,
    client_type: str, 
    services: ServiceContainer = Depends(get_websocket_service_container)
):
    try:
        await websocket.accept()
        await manager.connect(websocket, session_id, client_type)
        
        # Send initial chat history
        chat_history = await services.get_chat_history(session_id, None)
        recent_messages = await chat_history.get_recent_turns(20)
        
        # Preparing chat history in a format that frontend can easily use
        formatted_messages = [
            {
            "role": msg.get("role", "unknown"),
            "content": msg.get("content", ""),
            "timestamp": (
                msg.get("timestamp").isoformat() 
                if msg.get("timestamp") 
                else ""
            ),
            "customer_id": msg.get("customer_id", ""),
            "session_id": msg.get("session_id", "")
            }
            for msg in recent_messages
        ]
        
        await websocket.send_json({
            "type": "history",
            "messages": formatted_messages
        })
        
        # Keep connection alive, handle client messages
        while True:
            # wait for incoming message from frontend
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            # Handle different message types
            if message_data.get("type") == "message":
                content = message_data.get("content")
                customer_id = message_data.get("customer_id", "")
                
                if client_type == "customer":
                    await handle_customer_message(
                        services, session_id, customer_id, content, manager
                    )
                elif client_type == "staff":
                    await handle_staff_message(
                        services, session_id, customer_id, content, manager
                    )
            elif (
                message_data.get("type") == "command" 
                and client_type == "staff"
            ):
                # Only staff can send commands
                action = message_data.get("action")
                customer_id = message_data.get("customer_id", "")
                await handle_command(
                    services, session_id, customer_id, action, manager
                )
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id} - {client_type}")
        await manager.disconnect(websocket, session_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket.client_state != WebSocketState.DISCONNECTED:
            try:
                await websocket.close(code=1011)  # 1011 = Internal Error
            except Exception as close_error:
                logger.error(f"Error closing WebSocket: {close_error}")
        await manager.disconnect(websocket, session_id)