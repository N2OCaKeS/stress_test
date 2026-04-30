"""Domain constants shared across the service."""


class ActorType:
    USER = "user"
    BOT = "bot"
    SERVICE = "service"
    ANONYMOUS = "anonymous"


class EventStatus:
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class Severity:
    # Routine reads and successful non-admin operations
    INFO = "INFO"
    # Failed auth, denied access, suspicious patterns
    WARNING = "WARNING"
    # Admin operations that change platform state or security posture
    CRITICAL = "CRITICAL"
