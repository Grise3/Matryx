from typing import List, Dict, Any, Optional

class CommandContext:
    """Execution context for a command.
    
    This class is passed as an argument to each command and provides useful methods
    to interact with the room and the user.
    """
    
    def __init__(self, client, event, command_name: str, args: List[str], command: dict):
        """Initialise le contexte de la commande.
        
        Attributes:
            message (dict): Dictionary containing message information
            author (User): Object representing the message author
            channel (str): ID of the room where the message was sent
            client (Client): Client instance
            command (dict): Details of the executed command
            name (str): Command name
            args (List[str]): Command arguments
            prefix (str): Command prefix used
        """
        self.client = client
        self.event = event
        self.command = command
        self.name = command_name
        self.args = args
        self.prefix = client.command_prefix
        
        self.message = {
            'id': event.event_id,
            'content': getattr(event, 'content', {}),
            'room_id': getattr(event, 'room_id', None),
            'timestamp': getattr(event, 'timestamp', None),
            'raw': event.__dict__
        }
        
        self.channel = event.room_id
        
        self._author = None
        self._author_id = event.sender
        
    async def reply(self, content, **kwargs):
        """Reply to the message in the same room."""
        return await self.client.send_message(self.channel, {
            "msgtype": "m.text",
            "body": content,
            **kwargs
        })
        
    async def send(self, content: str, **kwargs):
        """Alias of reply()."""
        return await self.reply(content, **kwargs)
        
    def get_arg(self, index: int, default: Any = None) -> Any:
        """Get an argument by its index."""
        try:
            return self.args[index]
        except IndexError:
            return default
            
    def get_args(self) -> List[str]:
        """Get all arguments."""
        return self.args
        
    @property
    async def author(self):
        """Get the user who sent the command."""
        if self._author is None:
            self._author = await self.client.get_user(self._author_id)
        return self._author
        
    async def get_author(self):
        """Alias for the author property (for backward compatibility)."""
        return await self.author
        
    async def get_channel(self):
        """Get the room where the command was executed."""
        return await self.client.get_room(self.channel)
        
    async def reply_file(self, file_path: str, **kwargs):
        """Send a file as a reply to the current message.
        
        Args:
            file_path: Path to the file to send
            **kwargs: Additional arguments to pass to send_message
            
        Returns:
            str: The event ID of the sent message
            
        Example:
            # In a command
            await ctx.reply_file("/path/to/image.png")
        """
        return await self.client.send_message(
            self.channel,
            file=file_path,
            **kwargs
        )
