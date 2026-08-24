class AppError(Exception):
    code = "APP_ERROR"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvalidUploadRequest(AppError):
    code = "INVALID_UPLOAD_REQUEST"
