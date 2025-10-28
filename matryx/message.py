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
        
        # Pour les réponses
        self.in_reply_to = kwargs.get('in_reply_to')
        self.thread_id = kwargs.get('thread_id')
        
        # Si on a des pièces jointes mais pas de contenu, on crée un contenu par défaut
        if self.attachments and not self.content:
            self.content = "Pièce jointe: " + ", ".join(
                a.get('filename', 'fichier') for a in self.attachments
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
        Ajoute une pièce jointe au message.
        
        Args:
            file_path: Chemin local vers le fichier à joindre
            url: URL MXC du fichier (si déjà uploadé)
            filename: Nom du fichier
            mimetype: Type MIME du fichier
            size: Taille du fichier en octets
            width: Largeur (pour les images)
            height: Hauteur (pour les images)
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
            
        # Déterminer le type de message en fonction du MIME type
        if mimetype and mimetype.startswith('image/'):
            attachment['msgtype'] = 'm.image'
        elif mimetype and mimetype.startswith('video/'):
            attachment['msgtype'] = 'm.video'
        elif mimetype and mimetype.startswith('audio/'):
            attachment['msgtype'] = 'm.audio'
        else:
            attachment['msgtype'] = 'm.file'
            
        self.attachments.append(attachment)
        
        # Mettre à jour le contenu du message si nécessaire
        if not self.content:
            self.content = f"Pièce jointe: {filename}"
    
    async def add_reaction(self, emoji: str) -> None:
        """Add a reaction to this message."""
        if not self.room or not self.event_id:
            raise MatrixError("Unable to add reaction: message not linked to a room or without event ID")
            
        await self.room.add_reaction(self.event_id, emoji)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert the message to a dictionary compatible with the Matrix API."""
        if self.attachments:
            # Si on a des pièces jointes, on envoie un message avec pièce jointe
            if len(self.attachments) == 1:
                # Un seul fichier
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
                
                # Ajout des dimensions pour les images
                if attachment.get('width') and attachment.get('height'):
                    data['info'].update({
                        'w': attachment['width'],
                        'h': attachment['height']
                    })
                
                # Ajout du contenu en texte clair si disponible
                if self.content:
                    data['body'] = self.content
                    data['filename'] = attachment.get('filename')
            else:
                # Plusieurs fichiers, on envoie un message texte avec les liens
                file_list = "\n".join(
                    f"- {a.get('filename')} ({a.get('size', 0) // 1024} KB)" 
                    for a in self.attachments
                )
                data = {
                    'msgtype': 'm.text',
                    'body': f"{self.content}\n\nFichiers joints :\n{file_list}"
                }
        else:
            # Message texte simple
            data = {
                'msgtype': self.msgtype,
                'body': self.content,
                **self.extra_data
            }
        
        # Gestion des réponses
        if self.in_reply_to:
            if 'm.relates_to' not in data:
                data['m.relates_to'] = {}
            data['m.relates_to'].update({
                'm.in_reply_to': {
                    'event_id': self.in_reply_to
                }
            })
        
        return data
