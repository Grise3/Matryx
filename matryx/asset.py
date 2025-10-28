from typing import Optional, Union, Dict, Any, TYPE_CHECKING
import logging
import base64
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

if TYPE_CHECKING:
    from .client import Client

logger = logging.getLogger(__name__)

class MatrixAsset:
    """Represents a media asset on Matrix (avatar, file, etc.)."""
    
    def __init__(self, client: 'Client', mxc_url: str):
        self._client = client
        self.mxc_url = mxc_url
        self._parsed_url = urlparse(mxc_url)
        
        if not self._parsed_url.scheme == 'mxc':
            raise ValueError(f"Invalid MXC URL: {mxc_url}")
            
        self.server = self._parsed_url.netloc
        self.media_id = self._parsed_url.path.lstrip('/')
        self._data: Optional[bytes] = None
        
    def __str__(self) -> str:
        return self.mxc_url
        
    def __eq__(self, other) -> bool:
        return isinstance(other, MatrixAsset) and self.mxc_url == other.mxc_url
        
    def __hash__(self) -> int:
        return hash(self.mxc_url)
        
    @property
    def url(self) -> str:
        """Returns the direct URL to the media."""
        return self._build_url()
        
    def with_size(self, width: int, height: int, method: str = 'crop') -> 'MatrixAsset':
        """Returns a new asset with the specified size."""
        return MatrixAsset(
            self._client,
            f"{self.mxc_url}?width={width}&height={height}&method={method}"
        )
        
    def with_format(self, format: str) -> 'MatrixAsset':
        """Returns a new asset with the specified format."""
        if format.lower() not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            raise ValueError(f"Unsupported format: {format}")
            
        parsed = self._parsed_url._replace(
            path=f"{self._parsed_url.path}.{format}"
        )
        return MatrixAsset(self._client, urlunparse(parsed))
        
    def _build_url(self, width: Optional[int] = None, 
                  height: Optional[int] = None,
                  method: str = 'crop') -> str:
        """Builds the URL for this asset with optional resizing."""
        base_url = self._client.homeserver.rstrip('/')
        path = f"_matrix/media/v3/download/{self.server}/{self.media_id}"
        
        if width is not None and height is not None:
            path = f"_matrix/media/v3/thumbnail/{self.server}/{self.media_id}"
            query = urlencode({
                'width': width,
                'height': height,
                'method': method
            })
            return f"{base_url}/{path}?{query}"
            
        return f"{base_url}/{path}"
        
    async def read(self) -> bytes:
        """Downloads and returns the binary content of this asset."""
        if self._data is None:
            url = self._build_url()
            try:
                async with self._client._session.get(url) as response:
                    if response.status == 200:
                        self._data = await response.read()
                    else:
                        raise ValueError(f"Failed to download asset: HTTP {response.status}")
            except Exception as e:
                logger.error(f"Error downloading asset: {e}")
                raise
                
        return self._data
        
    async def to_data_url(self) -> str:
        """Returns the asset as a data URL (base64 encoded)."""
        data = await self.read()
        mime_type = self._guess_mime_type()
        return f"data:{mime_type};base64,{base64.b64encode(data).decode('utf-8')}"
        
    def _guess_mime_type(self) -> str:
        """Guesses the MIME type based on the file extension."""
        ext = self.mxc_url.split('.')[-1].lower()
        return {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }.get(ext, 'application/octet-stream')
