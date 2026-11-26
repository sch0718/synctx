class SyncError(Exception):
    """Sync Engine 에러 with 분류."""

    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type
