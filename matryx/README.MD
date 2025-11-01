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
                    f"https://matrix.org/_matrix/client/v1/media/download/"
                    f"{server_name}/{media_id}?access_token={self._client.access_token}"
                )
        return self._avatar_url
