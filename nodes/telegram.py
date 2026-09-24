"""Senders/editors adapted from SwissCore92/comfyui-telegram-suite (MIT).

The original socket names, widget definitions, return types and function names
are retained, except that bot becomes the bot_token STRING widget.
See THIRD_PARTY_NOTICES.md for the small internal compatibility fixes.
"""

import mimetypes
from typing import Any
from comfy_execution.graph import ExecutionBlocker
from .sender_retry import RetryingTelegramClient as TelegramClient, sender_network_guard
from . import inputs, utils

_CATEGORY = "Autonomous Telegram Bot ◀️"


class APIMethod:
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "method_name": inputs.method_name,
            },
            "optional": {
                "chat_id": inputs.chat_id,
                "params": inputs.params
            }
        }
     
    RETURN_TYPES = ("*",)
    RETURN_NAMES = ("RESULT *",)

    FUNCTION = "call_api_method"
    CATEGORY = f"{_CATEGORY}/experimental"

    @sender_network_guard
    def call_api_method(self, bot_token: str, method_name, chat_id=None, params=None):
        bot = TelegramClient(bot_token)
        params = dict(params or {})
        if chat_id is not None:
            params["chat_id"] = chat_id
        return (bot(method_name, params=params),)


class SendGeneric:
    OUTPUT_NODE = True

    RETURN_TYPES = ("DICT", "INT", "*")
    RETURN_NAMES = ("message", "message_id", "trigger")

    CATEGORY = _CATEGORY

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")
    
    @classmethod
    def VALIDATE_INPUTS(cls, input_types):
        return True

class SendMessage(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "text": inputs.text,
                "parse_mode": inputs.parse_mode,
                "disable_notification": inputs.disable_notification,
                "protect_content": inputs.protect_content,
                "message_thread_id": inputs.message_thread_id
            },
            "optional": {
                "trigger": inputs.trigger,
            }
        }

    FUNCTION = "send_message"

    @sender_network_guard
    def send_message(self, bot_token: str, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)
        
        if not str(params.get("text", "")).strip():
            return tuple(ExecutionBlocker(None) for _ in self.RETURN_TYPES)
        message = bot("sendMessage", params=params)

        return message, message["message_id"], trigger

class SendImage(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "IMAGE": inputs.image,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
                "has_spoiler": inputs.has_spoiler,
                "disable_notification": inputs.disable_notification,
                "protect_content": inputs.protect_content,
                "group": inputs.group,
                "send_as_file": inputs.send_as_file,
                "file_name": inputs.file_name("image"),
                "format": inputs.image_formats,
                "message_thread_id": inputs.message_thread_id,
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }

    FUNCTION = "send_photo"

    @sender_network_guard
    def send_photo(self, bot_token: str, IMAGE, group, send_as_file, file_name, format, trigger=None, **params):
        bot = TelegramClient(bot_token)
        id = "document" if send_as_file else "photo"

        params = utils.cleanup_params(params)

        images_bytes = utils.images_to_bytes(IMAGE, format)
        if group and len(images_bytes) > 10:
            raise ValueError("Telegram media groups allow at most 10 images; set group=False for larger batches.")

        if len(images_bytes) == 1:
            # Single Image
            image_bytes = images_bytes[0]
            params[id] = f"attach://{id}"
            file_name = file_name or "image"
            file_name = f"{file_name}.{format.lower()}"
            message = bot(
                "sendDocument" if send_as_file else "sendPhoto", 
                params=params, 
                files={id: (file_name, image_bytes, utils.guess_mimetype(file_name))}
            )
            return message, message["message_id"], trigger

        else:
            if group:
                # Multiple images - send as media group
                media = []
                files = {}
                file_name = file_name or "image"
                for i, b in enumerate(images_bytes):
                    name = f"{file_name}{i}.{format.lower()}"
                    files[f"{id}{i}"] = (name, b, utils.guess_mimetype(name))

                    m: dict[str, Any] = {
                        "type": "document" if send_as_file else "photo", 
                        "media": f"attach://{id}{i}",
                    }
                    if params.get("caption"):
                        m["caption"] = params["caption"]
                    if params.get("parse_mode", "None") != "None":
                        m["parse_mode"] = params["parse_mode"]
                    if id != "document":
                        m["show_caption_above_media"] = params["show_caption_above_media"]
                        m["has_spoiler"] = params.get("has_spoiler", False)

                    media.append(m)

                messages = bot(
                    "sendMediaGroup", 
                    params={
                        "chat_id": params["chat_id"], 
                        "media": media,
                        "message_thread_id": params.get("message_thread_id"),
                        "disable_notification": params["disable_notification"] or None,
                        "protect_content": params["protect_content"] or None,
                    },
                    files=files
                )
                return messages[-1], messages[-1]["message_id"], trigger

            else:
                # Multiple images - send individually
                messages = []
                for index, image_bytes in enumerate(images_bytes):
                    params[id] = f"attach://{id}"
                    name = f"{file_name}_{index}.{format.lower()}"

                    if id == "document":
                        params.pop("show_caption_above_media", None)

                    messages.append(
                        bot(
                            "sendDocument" if send_as_file else "sendPhoto", 
                            params=params, 
                            files={id: (name, image_bytes, utils.guess_mimetype(name))}
                        )
                    )
                return messages[-1], messages[-1]["message_id"], trigger

class SendVideo(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "video": inputs.video,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
                "has_spoiler": inputs.has_spoiler,
                "disable_notification": inputs.disable_notification,
                "protect_content": inputs.protect_content,
                "send_as": inputs.send_video_as,
                "message_thread_id": inputs.message_thread_id
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }

    FUNCTION = "send_video"

    @sender_network_guard
    def send_video(self, bot_token: str, video, send_as, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)
        
        file_path = utils.video_file_path(video)

        file_name = utils.media_basename(file_path)
        mimetype = mimetypes.guess_type(file_name)[0] or "application/octet_stream"

        if send_as == "File":
            send_as = "Document"

        id = send_as.lower()

        params[id] = f"attach://{id}"

        with open(file_path, "rb") as f:
            message = bot(
                f"send{send_as}",
                params=params,
                files={id: (file_name, f.read(), mimetype)}
            )
        
        return message, message["message_id"], trigger

class SendAudio(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "audio": inputs.audio,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
                "disable_notification": inputs.disable_notification,
                "protect_content": inputs.protect_content,
                "send_as": inputs.send_audio_as,
                "file_name": inputs.file_name("audio"),
                "message_thread_id": inputs.message_thread_id
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }

    FUNCTION = "send_audio"

    @sender_network_guard
    def send_audio(self, bot_token: str, audio, send_as, file_name, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)

        params["caption"] = params["caption"] or None

        format = "ogg" if send_as == "Voice" else "mp3" if send_as == "Audio" else "wav"

        wav_bytes = utils.audio_to_wav_bytes(audio)

        file_name = file_name or "audio"

        name = f"{file_name}.{format}"

        if send_as == "File":
            params["document"] = "attach://document"
            message = bot("sendDocument", params=params, files={"document": (name, wav_bytes, utils.guess_mimetype(name))})
            return message, message["message_id"], trigger
        
        id = "audio" if send_as == "Audio" else "voice"
        params[id] = f"attach://{id}"

        b = utils.convert_wav_bytes(wav_bytes, "mp3" if send_as == "Audio" else "ogg")

        message = bot(f"send{id.capitalize()}", params=params, files={id: (name, b, utils.guess_mimetype(name))})
        
        return message, message["message_id"], trigger


class EditMessageText(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "message_id": inputs.message_id,
                "text": inputs.text,
                "parse_mode": inputs.parse_mode,
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }
    CATEGORY = f"{_CATEGORY}/edit"
    FUNCTION = "edit_message_text"

    @sender_network_guard
    def edit_message_text(self, bot_token: str, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)
        
        message = bot("editMessageText", params=params)

        return message, message["message_id"], trigger

class EditMessageCaption(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "message_id": inputs.message_id,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
            },
            "optional": {
                "trigger": ("*", {"forceInput": True})
            }
        }

    CATEGORY = f"{_CATEGORY}/edit"
    FUNCTION = "edit_message_caption"

    @sender_network_guard
    def edit_message_caption(self, bot_token: str, trigger=None, **params):
        bot = TelegramClient(bot_token)
        if params["parse_mode"] == "None":
            params.pop("parse_mode")
        
        message = bot("editMessageCaption", params=params)

        return message, message["message_id"], trigger

class EditMessageImage(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "message_id": inputs.message_id,
                "IMAGE": inputs.image,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
                "file_name": inputs.file_name("image"),
                "format": inputs.image_formats,
                "as_file": inputs.send_as_file,
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }
    
    CATEGORY = f"{_CATEGORY}/edit"
    FUNCTION = "edit_message_image"

    @sender_network_guard
    def edit_message_image(self, bot_token: str, IMAGE, file_name, format,  as_file, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)
        
        name = f"{file_name}.{format.lower()}"
        
        _params = {
            "chat_id": params["chat_id"],
            "message_id": params["message_id"],
            "media": {k: v for k, v in {
                "type": "document" if as_file else "photo",
                "media": "attach://media",
                "caption": params.get("caption"),
                "parse_mode": params.get("parse_mode"),
                "show_caption_above_media": params.get("show_caption_above_media")
            }.items() if v is not None}
        }

        b = utils.images_to_bytes(IMAGE, format)[0]

        files = {"media": (name, b, utils.guess_mimetype(name))}

        message = bot("editMessageMedia", params=_params, files=files)

        return message, message["message_id"], trigger

class EditMessageVideo(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "message_id": inputs.message_id,
                "video": inputs.video,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
                "send_as": inputs.send_video_as,
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }
    
    CATEGORY = f"{_CATEGORY}/edit"
    FUNCTION = "edit_message_video"

    @sender_network_guard
    def edit_message_video(self, bot_token: str, video, send_as, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)

        file_path = utils.video_file_path(video)
        file_name = utils.media_basename(file_path)

        if send_as == "File":
            send_as = "Document"

        id = send_as.lower()
        
        _params = {
            "chat_id": params["chat_id"],
            "message_id": params["message_id"],
            "media": {k: v for k, v in {
                "type": id,
                "media": "attach://media",
                "caption": params.get("caption"),
                "parse_mode": params.get("parse_mode"),
                "show_caption_above_media": params.get("show_caption_above_media")
            }.items() if v is not None}
        }

        with open(file_path, "rb") as f:
            files = {"media": (file_name, f.read(), utils.guess_mimetype(file_name))}
            message = bot("editMessageMedia", params=_params, files=files)
            return message, message["message_id"], trigger

class EditMessageAudio(SendGeneric):
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "message_id": inputs.message_id,
                "audio": inputs.audio,
                "caption": inputs.caption,
                "parse_mode": inputs.parse_mode,
                "show_caption_above_media": inputs.show_caption_above_media,
                "file_name": inputs.file_name("audio"),
                "as_file": inputs.send_as_file,
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }
    
    CATEGORY = f"{_CATEGORY}/edit"
    FUNCTION = "edit_message_audio"

    @sender_network_guard
    def edit_message_audio(self, bot_token: str, audio, file_name, as_file, trigger=None, **params):
        bot = TelegramClient(bot_token)
        params = utils.cleanup_params(params)
        
        ext = "wav" if as_file else "mp3"
        
        name = f"{file_name}.{ext}"

        _params = {
            "chat_id": params["chat_id"],
            "message_id": params["message_id"],
            "media": {k: v for k, v in {
                "type": "document" if as_file else "audio",
                "media": "attach://media",
                "caption": params.get("caption"),
                "parse_mode": params.get("parse_mode"),
                "show_caption_above_media": params.get("show_caption_above_media")
            }.items() if v is not None}
        }

        b = utils.audio_to_wav_bytes(audio)
        if not as_file:
            b = utils.convert_wav_bytes(b)

        files = {"media": (name, b, utils.guess_mimetype(name))}

        message = bot("editMessageMedia", params=_params, files=files)

        return message, message["message_id"], trigger

class SendChatAction:
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, *args, **kwargs):
        return float("nan")
    
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bot_token": inputs.bot_token,
                "chat_id": inputs.chat_id,
                "action": inputs.chat_action,
                "message_thread_id": inputs.message_id,
            },
            "optional": {
                "trigger": inputs.trigger
            }
        }
    
    RETURN_TYPES = ("BOOL", "*")
    RETURN_NAMES = ("True", "trigger")

    FUNCTION = "send_chat_action"
    CATEGORY = _CATEGORY

    @sender_network_guard
    def send_chat_action(self, bot_token: str, trigger=None, **params):
        bot = TelegramClient(bot_token)
        result = bot("sendChatAction", params=utils.cleanup_params(params))
        return result, trigger
    
    def get_return_types(self, trigger_type, **kwargs):
        return ("BOOL", trigger_type)

