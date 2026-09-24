
from .nodes.receiver import TelegramListener
from .nodes import telegram 
from .nodes import utils

NODE_CLASS_MAPPINGS = {
    f"TelegramAutonomous_{k}": v for k, v in {
        "Listener": TelegramListener,
        "APIMethod": telegram.APIMethod,
        "SendMessage": telegram.SendMessage,
        "SendImage": telegram.SendImage,
        "SendVideo": telegram.SendVideo,
        "SendAudio": telegram.SendAudio,
        "SendChatAction": telegram.SendChatAction,

        "EditMessageText": telegram.EditMessageText,
        "EditMessageCaption": telegram.EditMessageCaption,
        "EditMessageImage": telegram.EditMessageImage,
        "EditMessageVideo": telegram.EditMessageVideo,
        "EditMessageAudio": telegram.EditMessageAudio,

        "ParseJSON": utils.ParseJSON,
    }.items()
}

NODE_DISPLAY_NAME_MAPPINGS = {
    f"TelegramAutonomous_{k}": f"TG {v} ◀️" for k, v in {
        "Listener": "Telegram Listener",
        "APIMethod": "API Method",
        "SendMessage": "Send Message",
        "SendImage": "Send Image(s)",
        "SendVideo": "Send Video",
        "SendAudio": "Send Audio",
        "SendChatAction": "Send Chat Action",

        "EditMessageText": "Edit Message Text",
        "EditMessageCaption": "Edit Message Caption",
        "EditMessageImage": "Edit Message Image",
        "EditMessageVideo": "Edit Message Video",
        "EditMessageAudio": "Edit Message Audio",

        "ParseJSON": "Parse JSON",
    }.items()
}

__all__ = (
    "NODE_CLASS_MAPPINGS", 
    "NODE_DISPLAY_NAME_MAPPINGS", 
)
