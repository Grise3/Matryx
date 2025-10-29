from typing import Optional, Dict, Any, List, TYPE_CHECKING
import os
from .exceptions import MatrixError
from .asset import MatrixAsset

if TYPE_CHECKING:
    from .room import Room
    from .user import User
    from .client import Client

class Message:
    """Represents a Matrix message."""
    
    def __init__(
        self,
        content: str = "",
        msgtype: str = "m.text",
        room: Optional['Room'] = None,
        sender: Optional['User'] = None,
        event_id: Optional[str] = None,
        **kwargs
    ):
        self.content = content
        self.msgtype = msgtype
        self.room = room
        self.sender = sender
        self.event_id = event_id
        self.extra_data = kwargs
        self.attachments: List[Dict[str, Any]] = kwargs.get('attachments', [])
        
        self.in_reply_to = kwargs.get('in_reply_to')
        self.thread_id = kwargs.get('thread_id')
        
        if self.attachments and not self.content:
            self.content = "Attachment: " + ", ".join(
                a.get('filename', 'file') for a in self.attachments
            )
    
    def __str__(self) -> str:
        return f"<Message id='{self.event_id}' type='{self.msgtype}'>"
    
    async def reply(self, content: str, **kwargs) -> 'Message':
        """Reply to this message."""
        if not self.room:
            raise MatrixError("Unable to reply: message not linked to a room")
            
        return await self.room.send_message(
            content,
            in_reply_to=self.event_id,
            **kwargs
        )
    
    def add_attachment(
        self,
        file_path: Optional[str] = None,
        *,
        url: Optional[str] = None,
        filename: Optional[str] = None,
        mimetype: Optional[str] = None,
        size: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> None:
        """
        Add an attachment to the message.
        
        Args:
            file_path: Local file path to attach
            url: MXC URL of the file (if already uploaded)
            filename: File name
            mimetype: MIME type of the file
            size: File size in bytes
            width: Image width (for images)
            height: Image height (for images)
        """
        if file_path:
            if not os.path.isfile(file_path):
                raise FileNotFoundError(f"File not found: {file_path}")
                
            if not filename:
                filename = os.path.basename(file_path)
                
            if not mimetype:
                import mimetypes
                mimetype, _ = mimetypes.guess_type(file_path) or 'application/octet-stream'
                
            if size is None:
                size = os.path.getsize(file_path)
                
        elif not url:
            raise ValueError("Either file_path or url must be provided")
            
        attachment = {
            'url': url,
            'filename': filename,
            'mimetype': mimetype,
            'size': size,
        }
        
        if width and height:
            attachment.update({
                'width': width,
                'height': height
            })
            
        if mimetype and mimetype.startswith('image/'):
            attachment['msgtype'] = 'm.image'
        elif mimetype and mimetype.startswith('video/'):
            attachment['msgtype'] = 'm.video'
        elif mimetype and mimetype.startswith('audio/'):
            attachment['msgtype'] = 'm.audio'
        else:
            attachment['msgtype'] = 'm.file'
            
        self.attachments.append(attachment)
        
        if not self.content:
            self.content = f"Attachment: {filename}"
    
    async def add_reaction(self, emoji: str) -> None:
        """Add a reaction to this message."""
        if not self.room or not self.event_id:
            raise MatrixError("Unable to add reaction: message not linked to a room or without event ID")
            
        await self.room.add_reaction(self.event_id, emoji)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the message to a dictionary compatible with the Matrix API."""
        if self.attachments:
            if len(self.attachments) == 1:
                attachment = self.attachments[0]
                data = {
                    'msgtype': attachment.get('msgtype', 'm.file'),
                    'body': attachment.get('filename', 'fichier'),
                    'filename': attachment.get('filename'),
                    'url': attachment.get('url'),
                    'info': {
                        'size': attachment.get('size'),
                        'mimetype': attachment.get('mimetype')
                    }
                }
                
                if attachment.get('width') and attachment.get('height'):
                    data['info'].update({
                        'w': attachment['width'],
                        'h': attachment['height']
                    })
                
                if self.content:
                    data['body'] = self.content
                    data['filename'] = attachment.get('filename')
            else:
                file_list = "\n".join(
                    f"- {a.get('filename')} ({a.get('size', 0) // 1024} KB)" 
                    for a in self.attachments
                )
                data = {
                    'msgtype': 'm.text',
                    'body': f"{self.content}\n\nFichiers joints :\n{file_list}"
                }
        else:
            data = {
                'msgtype': self.msgtype,
                'body': self.content,
                **self.extra_data
            }
        
        if self.in_reply_to:
            if 'm.relates_to' not in data:
                data['m.relates_to'] = {}
            data['m.relates_to'].update({
                'm.in_reply_to': {
                    'event_id': self.in_reply_to
                }
            })
        
        return data
