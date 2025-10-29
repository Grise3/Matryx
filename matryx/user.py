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
        self._profile_loaded = False
    
    def __str__(self) -> str:
        return f"<User id='{self.id}'>"
    
    def __eq__(self, other) -> bool:
        return isinstance(other, User) and self.id == other.id
    
    def __hash__(self) -> int:
        return hash(self.id)
    
    @property
    async def avatar_url(self) -> Optional[str]:
        """Returns the URL of the user's avatar, or None if they have no avatar.
        
        Returns:
            Optional[str]: The URL of the avatar or None if not set
            
        Example:
            # Get avatar URL
            url = await user.avatar_url
            if url:
                print(f"Avatar URL: {url}")
        """
        await self._ensure_profile_loaded()
        return self.get_avatar_url()
        

        
    def get_avatar_url(self, width: int = 128, height: int = 128) -> Optional[str]:
        """Returns the HTTPS URL for the user's avatar with optional resizing.
        
        Args:
            width: Width of the thumbnail in pixels
            height: Height of the thumbnail in pixels
            
        Returns:
            Optional[str]: The HTTPS URL of the avatar or None if not set
        """
        if not self._avatar_url:
            return None
            
        if self._avatar_url.startswith('http'):
            return self._avatar_url
            
        if self._avatar_url.startswith('mxc://'):
            mxc_parts = self._avatar_url.replace('mxc://', '').split('/', 1)
            if len(mxc_parts) == 2:
                server_name = mxc_parts[0]
                media_id = mxc_parts[1]
                media_id = media_id.replace('/', '%2F')
                return (
                    f"https://matrix-client.matrix.org/_matrix/media/v3/thumbnail/"
                    f"{server_name}/{media_id}?width={width}&height={height}"
                    "&method=crop&allow_redirect=true"
                )
        return self._avatar_url
        
    @property
    def avatar(self) -> Optional['MatrixAsset']:
        """Returns the user's avatar as a MatrixAsset, or None if they have no avatar.
        
        Example:
            # Save user's avatar to a file
            avatar = await user.avatar.read()
            with open('avatar.png', 'wb') as f:
                f.write(avatar)
        """
        if not self._avatar_url:
            return None
        from .asset import MatrixAsset
        return MatrixAsset(self._avatar_url, self._client)
    
    async def _ensure_profile_loaded(self):
        """S'assure que le profil utilisateur est chargé."""
        if not self._profile_loaded:
            try:
                profile = await self._client.get_profile(self.id)
                if profile:
                    self._display_name = profile.get('displayname')
                    self._avatar_url = profile.get('avatar_url')
                self._profile_loaded = True
            except Exception as e:
                logger.warning(f"Erreur lors du chargement du profil de {self.id}: {e}")
    
    async def displayname(self) -> str:
        """Get the display name of the user."""
        if self._display_name is None:
            await self._ensure_profile_loaded()
            if self._display_name is None:
                return self.id.split(':', 1)[0].lstrip('@')
        return self._display_name
        
    async def get_avatar(self) -> Optional['MatrixAsset']:
        """Récupère l'avatar de l'utilisateur de manière asynchrone."""
        if not self._profile_loaded:
            await self._ensure_profile_loaded()
        return self.avatar
    
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
            
        server_media = mxc_url[6:].split('/', 1)
        if len(server_media) != 2:
            return None
            
        server, media_id = server_media
        
        base_url = self._client.homeserver.rstrip('/')
        
        if not allow_direct or base_url.endswith(server):
            return f"{base_url}/_matrix/media/v3/thumbnail/{server}/{media_id}?width={width}&height={height}&method={method}&allow_redirect=true"
            
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
