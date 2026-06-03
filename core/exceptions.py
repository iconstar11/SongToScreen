class StageError(Exception):
    def __init__(self, stage: int, message: str):
        self.stage = stage
        super().__init__(f"Stage {stage} failed: {message}")
