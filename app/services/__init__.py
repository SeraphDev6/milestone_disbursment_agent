"""Business logic. Raises ServiceError for anything the caller should see as an HTTP error."""


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
