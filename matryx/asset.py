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
            
        # Extract the server and media ID from the MXC URL
        # Expected format: mxc://server/media_id
        self.server = self._parsed_url.netloc
        self.media_id = self._parsed_url.path.lstrip('/')
        self._data: Optional[bytes] = None
        
        logger.debug(f"Initialized MatrixAsset - Server: {self.server}, Media ID: {self.media_id}")
        
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
        """Builds the URL for this asset with optional resizing.
        
        Args:
            width: Optional width for thumbnail
            height: Optional height for thumbnail
            method: Resize method ('crop' or 'scale')
            
        Returns:
            str: The full URL to download the asset
        """
        if self.server == 'matrix.org':
            base_url = 'https://matrix-client.matrix.org'
        else:
            base_url = self._client.homeserver.rstrip('/')
        
        path = f"_matrix/media/v3/download/{self.server}/{self.media_id}"
        if width is not None and height is not None:
            path = f"_matrix/media/v3/thumbnail/{self.server}/{self.media_id}"
            query = urlencode({
                'width': width,
                'height': height,
                'method': method,
                'allow_redirect': 'true'
            })
            url = f"{base_url}/{path}?{query}"
            logger.debug(f"Redimensionning URL generated: {url}")
            return url
            
        url = f"{base_url}/{path}?allow_redirect=true"
        logger.debug(f"Download URL generated: {url}")
        return url
        
    async def read(self) -> bytes:
        """Downloads and returns the binary content of this asset."""
        if self._data is None:
            if self.server == 'matrix.org':
                base_url = 'https://matrix-client.matrix.org'
            else:
                base_url = self._client.homeserver.rstrip('/')
            
            path = f"_matrix/client/v1/media/download/{self.server}/{self.media_id}"
            url = f"{base_url}/{path}"
            
            logger.debug(f"Downloading from: {url}")
            
            try:
                if not hasattr(self._client, '_session') or self._client._session is None:
                    raise ValueError("The HTTP session is not initialized in the client")
                
                headers = {
                    'Accept': '*/*',
                    'User-Agent': 'matryx/1.0',
                    'Authorization': f'Bearer {self._client.access_token}'
                }
                
                async with self._client._session.get(url, headers=headers) as response:
                    logger.debug(f"Answer received: {response.status} {response.reason}")
                    logger.debug(f"Response headers: {dict(response.headers)}")
                    
                    if response.status == 200:
                        content_type = response.headers.get('Content-Type', '')
                        
                        if content_type.startswith('multipart/mixed'):
                            reader = aiohttp.MultipartReader.from_response(response)
                            
                            metadata_part = await reader.next()
                            if metadata_part is not None:
                                metadata = await metadata_part.json()
                                logger.debug(f"Media metadata: {metadata}")
                            
                            media_part = await reader.next()
                            if media_part is not None:
                                if 'Location' in media_part.headers:
                                    media_url = media_part.headers['Location']
                                    logger.debug(f"Downloading from external URL: {media_url}")
                                    async with self._client._session.get(media_url, headers=headers) as media_response:
                                        if media_response.status == 200:
                                            self._data = await media_response.read()
                                        else:
                                            error_text = await media_response.text()
                                            logger.error(f"Failed to download external asset: HTTP {media_response.status} - {error_text}")
                                            raise ValueError(f"Failed to download external asset: HTTP {media_response.status}")
                                else:
                                    self._data = await media_part.read()
                        else:
                            self._data = await response.read()
                        
                        if self._data:
                            logger.debug(f"Download successful, data size: {len(self._data)} bytes")
                        else:
                            raise ValueError("No data received in the response")
                    else:
                        error_text = await response.text()
                        logger.error(f"Download failed: HTTP {response.status} - {error_text}")
                        raise ValueError(f"Failed to download asset: HTTP {response.status}")
                
            except Exception as e:
                logger.error(f"Error downloading asset: {str(e)}", exc_info=True)
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
