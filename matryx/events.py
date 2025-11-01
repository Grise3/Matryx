import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, TYPE_CHECKING, Union
from .message import Message
from .user import User
from .asset import MatrixAsset
import os

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .room import Room
    from .message import Message

class Event:
    """Base class for Matrix events."""
    
    def __init__(self, event_data: Dict[str, Any], room: 'Room' = None):
        self.raw_data = event_data
        self.event_id = event_data.get('event_id')
        self._sender_id = event_data.get('sender')
        self.origin_server_ts = event_data.get('origin_server_ts')
        self.unsigned = event_data.get('unsigned', {})
        self.room = room
        self.type = event_data.get('type')
        self.content = event_data.get('content', {})
        self._sender = None
        
        if hasattr(room, 'id'):
            self.room_id = room.id
        elif 'room_id' in event_data:
            self.room_id = event_data['room_id']
        else:
            self.room_id = None
        
        self._timestamp = None
        if self.origin_server_ts is not None:
            self._timestamp = datetime.fromtimestamp(self.origin_server_ts / 1000, tz=timezone.utc)
    
    @property
    def timestamp(self) -> Optional[datetime]:
        """Return the event timestamp in UTC."""
        return self._timestamp
    
    def format_timestamp(self, timezone_str: str = 'UTC', format_str: str = '%Y-%m-%d %H:%M:%S %Z') -> str:
        """Format the timestamp in the specified timezone.
        
        Args:
            timezone_str: Timezone name (e.g., 'Europe/Paris')
            format_str: Date format (default: '%Y-%m-%d %H:%M:%S %Z')
            
        Returns:
            The formatted timestamp as a string
        """
        if not self._timestamp:
            return 'N/A'
            
        try:
            import pytz
            tz = pytz.timezone(timezone_str)
            return self._timestamp.astimezone(tz).strftime(format_str)
        except ImportError:
            return self._timestamp.strftime(format_str)
    
    def __str__(self) -> str:
        timestamp = self.format_timestamp() if hasattr(self, 'format_timestamp') else 'no-timestamp'
        return f"<{self.__class__.__name__} id={self.event_id} type={self.type} ts={timestamp}>"
    
    @property
    def sender_id(self) -> str:
        """Retourne l'ID de l'expéditeur (sans faire d'appel réseau)."""
        return self._sender_id
    
    @property
    def sender(self) -> 'User':
        """
        Return the User object of the sender.
        """
        if self._sender is None and hasattr(self, 'room') and self.room and hasattr(self.room, 'client'):
            self._sender = self.room.client._get_user(self._sender_id)
            asyncio.create_task(self._sender._ensure_profile_loaded())
        return self._sender

class MessageEvent(Event):
    """Represents a message event in Matrix."""
    
    def __init__(self, event_data: Dict[str, Any], room: 'Room' = None):
        super().__init__(event_data, room)
        content = event_data.get('content', {})
        
        attachments = []
        if 'url' in content and content.get('msgtype') in ['m.image', 'm.file', 'm.video', 'm.audio']:
            attachment = {
                'url': content['url'],
                'filename': content.get('filename'),
                'mimetype': content.get('info', {}).get('mimetype'),
                'size': content.get('info', {}).get('size'),
                'width': content.get('info', {}).get('w'),
                'height': content.get('info', {}).get('h'),
                'msgtype': content.get('msgtype')
            }
            attachments.append(attachment)
        
        extra_content = content.copy()
        for field in ['body', 'msgtype', 'url', 'filename', 'info']:
            extra_content.pop(field, None)

        self.message = Message(
            content=content.get('body', ''),
            msgtype=content.get('msgtype', 'm.text'),
            room=room,
            sender=event_data.get('sender'),
            event_id=event_data.get('event_id'),
            attachments=attachments,
            **extra_content
        )
        
        if 'url' in content and content['url'].startswith('mxc://'):
            self.media_url = content['url']
            self.asset = MatrixAsset(room.client if room and hasattr(room, 'client') else None, self.media_url)
        else:
            self.media_url = None
            self.asset = None
    
    def __str__(self) -> str:
        if self.media_url:
            return f"<MessageEvent from='{self.message.sender}' type='{self.message.msgtype}' has_attachment=True>"
        return f"<MessageEvent from='{self.message.sender}' type='{self.message.msgtype}'>"
    
    async def download_attachment(self, output_path: Optional[str] = None) -> Optional[str]:
        """
        Télécharge la pièce jointe du message.
        
        Args:
            output_path: Chemin de sortie pour le fichier. Si non spécifié, utilise le nom du fichier d'origine.
            
        Returns:
            str: Chemin du fichier téléchargé, ou None si aucune pièce jointe
        """
        if not self.asset:
            return None
            
        try:
            data = await self.asset.read()
            
            if not output_path:
                if self.message.attachments and self.message.attachments[0].get('filename'):
                    output_path = self.message.attachments[0]['filename']
                else:
                    media_id = self.media_url.split('/')[-1]
                    ext = self.message.attachments[0].get('mimetype', '').split('/')[-1] if self.message.attachments else 'bin'
                    output_path = f"{media_id[:8]}.{ext}"
            
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            
            with open(output_path, 'wb') as f:
                f.write(data)
                
            return output_path
            
        except Exception as e:
            import logging
            logging.error(f"Error downloading attachment: {e}")
            return None

    async def reply(self, content: str, **kwargs) -> 'MessageEvent':
        """Reply to this message in the same room."""
        if not self.room:
            raise MatrixEventError("Unable to reply: event not linked to a room")
        return await self.room.send_message(content, **kwargs)

class ReactionEvent(Event):
    """Represents a reaction event to a message."""
    
    def __init__(self, event_data: Dict[str, Any], room: 'Room' = None):
        super().__init__(event_data, room)
        
        self._is_redaction = False
        self.relates_to = {}
        self.reaction_key = None
        self.event_id = None
        self.original_event_id = None
        
        if self.type == 'm.room.redaction' or 'redacts' in event_data:
            self._is_redaction = True
            
            self.original_event_id = event_data.get('redacts')
            self.event_id = event_data.get('event_id')
            
            redacted_because = self.unsigned.get('redacted_because', {})
            
            if 'content' in redacted_because:
                content = redacted_because['content']
                if 'm.relates_to' in content:
                    self.relates_to = content['m.relates_to']
                
                if 'key' in content and not self.relates_to:
                    self.relates_to = {'key': content['key']}
            
            if not self.relates_to and 'm.relates_to' in event_data:
                self.relates_to = event_data['m.relates_to']
            
            if isinstance(self.relates_to, dict):
                self.reaction_key = self.relates_to.get('key')
            
            if not self.reaction_key and 'key' in self.content:
                self.reaction_key = self.content['key']
            
            original_event_data = self.unsigned.get('original_event')
            if not self.reaction_key and original_event_data:
                if 'content' in original_event_data and 'm.relates_to' in original_event_data['content']:
                    relates_to = original_event_data['content']['m.relates_to']
                    if isinstance(relates_to, dict) and 'key' in relates_to:
                        self.reaction_key = relates_to['key']
                        if not self.relates_to:
                            self.relates_to = relates_to
            
            logger.debug(f"Details of deleted reaction - relates_to: {self.relates_to}, reaction_key: {self.reaction_key}")
        else:
            self.relates_to = self.content.get('m.relates_to', {})
            if isinstance(self.relates_to, dict):
                self.reaction_key = self.relates_to.get('key')
                self.original_event_id = self.relates_to.get('event_id')
            
            self.event_id = event_data.get('event_id')
        
        logger.debug(f"New ReactionEvent: type={self.type}, "
                   f"is_removal={self.is_removal}, "
                   f"reaction_key={self.reaction_key}, "
                   f"event_id={self.event_id}, "
                   f"original_event_id={self.original_event_id}")
    
    @property
    def is_addition(self) -> bool:
        """Check if it's an added reaction."""
        return self.type == 'm.reaction' and not self._is_redaction
    
    @property
    def is_removal(self) -> bool:
        """Check if it's a removed reaction."""
        return self._is_redaction or self.unsigned.get('redacted_because') is not None

# Dictionary of known event types
EVENT_TYPES = {
    'm.room.message': MessageEvent,
    'm.reaction': ReactionEvent,
}

def event_factory(event_data: Dict[str, Any], room: 'Room' = None) -> Event:
    """Create an appropriate event instance based on the event type."""
    event_type = event_data.get('type')
    event_class = EVENT_TYPES.get(event_type, Event)
    return event_class(event_data, room)
