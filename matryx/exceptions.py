class MatrixError(Exception):
    """Base class for Matrix library exceptions."""
    pass

class MatrixConnectionError(MatrixError):
    """Error connecting to the Matrix server."""
    pass

class MatrixAuthError(MatrixError):
    """Authentication error."""
    pass

class MatrixAPIError(MatrixError):
    """Error returned by the Matrix API."""
    def __init__(self, message, status_code=None, error_code=None):
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)

class ForbiddenError(MatrixAPIError):
    """Authorization error (access denied)."""
    def __init__(self, message="Access denied", status_code=403, error_code="M_FORBIDDEN"):
        super().__init__(message, status_code, error_code)

class NotFoundError(MatrixAPIError):
    """Error when the requested resource does not exist."""
    def __init__(self, message="Resource not found", status_code=404, error_code="M_NOT_FOUND"):
        super().__init__(message, status_code, error_code)

class MatrixRoomError(MatrixError):
    """Error related to Matrix rooms."""
    pass

class MatrixEventError(MatrixError):
    """Error related to Matrix events."""
    pass
