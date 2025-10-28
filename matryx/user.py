from typing import Optional, Dict, Any, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from .client import Client
    from .asset import MatrixAsset

logger = logging.getLogger(__name__)

class User:
    """Represents a Matrix user with its properties and methods."""
    
    def __init__(self, client: 'Client', user_id: str):
        self._client = client
        self.id = user_id
        self._display_name = None
        self._avatar_url = None
        self._presence = None
    
    def __str__(self) -> str:
        return f"<User id='{self.id}'>"
    
    def __eq__(self, other) -> bool:
        return isinstance(other, User) and self.id == other.id
    
    def __hash__(self) -> int:
        return hash(self.id)
    
    async def displayname(self) -> str:
        """Get the display name of the user."""
        if self._display_name is None:
            self._display_name = await self._client.get_displayname(self.id)
        return self._display_name or self.id.split(':', 1)[0].lstrip('@')
    
    def _mxc_to_http(self, mxc_url: str, width: int = 64, height: int = 64, method: str = 'crop', allow_direct: bool = True) -> Optional[str]:
        """Convert an MXC URL to an HTTP URL.
        
        Args:
            mxc_url: The MXC URL (format: mxc://server/mediaId)
            width: Desired width of the image
            height: Desired height of the image
            method: Resizing method ('crop', 'scale' or 'stretch')
            allow_direct: Allow direct access to the media server if necessary
            
        Returns:
            The HTTP URL of the image or None if the MXC URL is invalid
        """
        if not mxc_url or not mxc_url.startswith('mxc://'):
            return None
            
        # Extract the server and media ID
        server_media = mxc_url[6:].split('/', 1)
        if len(server_media) != 2:
            return None
            
        server, media_id = server_media
        
        # Build the download URL using the v3 API and allow_redirect
        base_url = self._client.homeserver.rstrip('/')
        
        # If we are on the same server or if we do not allow direct access
        if not allow_direct or base_url.endswith(server):
            # Use the v3 API with allow_redirect
            return f"{base_url}/_matrix/media/v3/thumbnail/{server}/{media_id}?width={width}&height={height}&method={method}&allow_redirect=true"
            
        # For remote servers
        return f"https://{server}/_matrix/media/v3/thumbnail/{server}/{media_id}?width={width}&height={height}&method={method}&allow_redirect=true"
    
    @property
    def avatar(self) -> Optional['MatrixAsset']:
        """Returns the user's avatar as a MatrixAsset, if any."""
        if not hasattr(self, '_avatar_asset') and self._avatar_url:
            from .asset import MatrixAsset
            self._avatar_asset = MatrixAsset(self._client, self._avatar_url)
        return getattr(self, '_avatar_asset', None)
    
    async def get_avatar(self) -> Optional['MatrixAsset']:
        """Fetches and returns the user's avatar as a MatrixAsset, if any."""
        if self._avatar_url is None:
            self._avatar_url = await self._client.get_avatar_url(self.id)
            if self._avatar_url:
                from .asset import MatrixAsset
                self._avatar_asset = MatrixAsset(self._client, self._avatar_url)
        return self.avatar
    
    async def download_avatar(self, output_path: str, width: int = 256, height: int = 256, method: str = 'crop') -> bool:
        """Download the user's avatar.
        
        Args:
            output_path: Path where to save the avatar
            width: Desired width of the avatar
            height: Desired height of the avatar
            method: Resizing method ('crop', 'scale' or 'stretch')
            
        Returns:
            True if the download was successful, False otherwise
        """
        avatar = await self.get_avatar()
        if not avatar:
            return False
            
        try:
            # If dimensions are specified, use with_size
            if width and height:
                avatar = avatar.with_size(width, height, method)
                
            data = await avatar.read()
            with open(output_path, 'wb') as f:
                f.write(data)
            return True
            
        except Exception as e:
            logger.error(f"Error downloading avatar: {e}")
            return False
    
    async def presence(self) -> Dict[str, Any]:
        """Get the user's presence status."""
        if self._presence is None:
            self._presence = await self._client.get_presence(self.id) or {}
        return self._presence
    
    @property
    def display_name(self) -> str:
        """Get the user's display name (may be the ID if not loaded)."""
        return self._display_name or self.id.split(':', 1)[0].lstrip('@')
    
    async def update_info(self) -> None:
        """Update all user information."""
        self._display_name = await self._client.get_displayname(self.id)
        self._avatar_url = await self._client.get_avatar_url(self.id)
        self._presence = await self._client.get_presence(self.id) or {}
    @property
    def mention(self) -> str:
        """Returns a formatted mention for this member."""
        return f"{self.display_name or self.id}"
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        """Create a user from a Matrix data dictionary."""
        return cls(
            user_id=data.get('user_id', ''),
            display_name=data.get('displayname'),
            avatar_url=data.get('avatar_url')
        )
