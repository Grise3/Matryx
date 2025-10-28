import asyncio
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from .message import Message
from .user import User
from .events import event_factory, MessageEvent, ReactionEvent
from .exceptions import MatrixRoomError

if TYPE_CHECKING:
    from .client import Client

class Room:
    """Represents a Matrix room."""
    
    def __init__(self, room_id: str, client: 'Client', **kwargs):
        self.id = room_id
        self._client = client
        self.name = kwargs.get('name')
        self.topic = kwargs.get('topic')
        self.aliases = kwargs.get('aliases', [])
        self.canonical_alias = kwargs.get('canonical_alias')
        self.members = {}  # user_id -> User
        self._typing_users = set()
        self._typing_task = None
        self._events = {}  # Cache of events by ID
    
    @property
    def client(self):
        """Retourne le client associé à cette salle."""
        return self._client
        
    def __str__(self) -> str:
        return f"<Room id='{self.id}' name='{self.name}'>"
    
    async def send_message(self, content: str, **kwargs) -> Message:
        """Send a message to this room."""
        message = Message(content, room=self, **kwargs)
        await self._client.send_message(self.id, message)
        return message
    
    async def add_reaction(self, event_id: str, emoji: str) -> None:
        """Add a reaction to a message."""
        await self._client.send_reaction(self.id, event_id, emoji)
    
    async def remove_reaction(self, event_id: str, emoji: str) -> None:
        """Remove a reaction from a message."""
        await self._client.redact_event(self.id, event_id, reason=f"Remove reaction: {emoji}")
    
    def add_event(self, event: Any) -> None:
        """Add an event to the room cache.
        
        Args:
            event: The event to add
        """
        if hasattr(event, 'event_id') and event.event_id:
            self._events[event.event_id] = event
    
    def get_event(self, event_id: str) -> Optional[Any]:
        """Get an event from the room cache by its ID.
        
        Args:
            event_id: The ID of the event to retrieve
            
        Returns:
            The event corresponding to the ID or None if it does not exist
        """
        return self._events.get(event_id)
    
    async def get_members(self) -> List[User]:
        """Get the list of members in the room."""
        members_data = await self._client.get_room_members(self.id)
        self.members = {}
        
        for member_data in members_data:
            user = User.from_dict(member_data)
            self.members[user.id] = user
        
        return list(self.members.values())
    
    async def get_member(self, user_id: str) -> Optional[User]:
        """Get a specific member of the room."""
        if not self.members:
            await self.get_members()
        return self.members.get(user_id)
    
    async def leave(self) -> None:
        """Leave this room."""
        await self._client.leave_room(self.id)
    
    async def set_topic(self, topic: str) -> None:
        """Set the topic of the room."""
        await self._client.set_room_topic(self.id, topic)
        self.topic = topic
    
    async def set_name(self, name: str) -> None:
        """Set the name of the room."""
        await self._client.set_room_name(self.id, name)
        self.name = name
    
    async def invite_user(self, user_id: str) -> None:
        """Invite a user to the room."""
        await self._client.invite_user(self.id, user_id)
    
    async def kick_user(self, user_id: str, reason: str = "") -> None:
        """Kick a user from the room."""
        await self._client.kick_user(self.id, user_id, reason)
    
    async def ban_user(self, user_id: str, reason: str = "") -> None:
        """Ban a user from the room."""
        await self._client.ban_user(self.id, user_id, reason)
    
    async def unban_user(self, user_id: str) -> None:
        """Unban a user from the room."""
        await self._client.unban_user(self.id, user_id)
    
    async def start_typing(self, timeout: float = 5.0) -> None:
        """Indicate that the user is typing."""
        if self._typing_task is not None:
            self._typing_task.cancel()
            
        await self._client.send_typing(self.id, True, timeout * 1000)
        
        # Schedule the stop of the typing indication
        self._typing_task = asyncio.create_task(self._stop_typing_after(timeout))
    
    async def _stop_typing_after(self, delay: float) -> None:
        """Stop indicating that the user is typing after a delay."""
        try:
            await asyncio.sleep(delay)
            await self._client.send_typing(self.id, False)
        except asyncio.CancelledError:
            # The task was cancelled because the user started typing again
            pass
        finally:
            self._typing_task = None
    
    async def stop_typing(self) -> None:
        """Stop indicating that the user is typing."""
        if self._typing_task is not None:
            self._typing_task.cancel()
            self._typing_task = None
        await self._client.send_typing(self.id, False)
