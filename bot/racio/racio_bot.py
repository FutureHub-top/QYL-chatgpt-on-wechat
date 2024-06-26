# access RACIO knowledge base platform
# docs: https://link-ai.tech/platform/link-app/wechat
import json
import time
import re
import requests

from bot.bot import Bot
from bot.chatgpt.chat_gpt_session import ChatGPTSession
from bot.openai.open_ai_image import OpenAIImage
from bot.session_manager import SessionManager
from bridge.context import Context, ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from config import conf, pconf


class RacioBot(Bot, OpenAIImage):
    # authentication failed
    AUTH_FAILED_CODE = 401
    NO_QUOTA_CODE = 406

    def __init__(self):
        super().__init__()
        self.sessions = SessionManager(ChatGPTSession, model=conf().get("model") or "gpt-3.5-turbo-16k")
        self.args = {}
        self.__dict = {}    # key pairs for session_id and conversation_id
        self.response_mode = conf().get("racio_response_mode", "blocking")

    def _get_conversation_id(self, session_id):
        return self.__dict.get(session_id, "")

    def _set_conversation_id(self, session_id, conversation_id):
        self.__dict[session_id] = conversation_id

    def _clear_conversation(self, session_id):
        if session_id in self.__dict:
            del self.__dict[session_id]

    def _clear_all_conversation(self):
        self.__dict.clear()

    def reply(self, query, context: Context = None) -> Reply:
        if context.type == ContextType.TEXT:
            logger.info("[RACIO-BOT] query={0}, context={1}".format(query, context))
            session_id = context["session_id"]
            reply = None
            
            # "clear_memory_commands": ["#清除记忆", "#清除所有", "#更新配置", "#showmemore"],
            if query == "#清除记忆":
                self.sessions.clear_session(session_id)
                self._clear_conversation(session_id)
                reply = Reply(ReplyType.INFO, "记忆已清除")
            elif query == "#清除所有":
                self.sessions.clear_all_session()
                self._clear_all_conversation()
                reply = Reply(ReplyType.INFO, "所有人记忆已清除")
            elif query == "#showmemore":
                if self._get_conversation_id(session_id):
                    reply = Reply(ReplyType.INFO, self._get_conversation_id(session_id))
                else:
                    reply = Reply(ReplyType.INFO, "No more conversations")
                    
            if reply:
                return reply
            else:
                return self._chat(query, context)
        elif context.type == ContextType.PATPAT:
            logger.info(f"[RACIO-BOT] Receive PATPAT type msg, query={query}, context.type={context.type}, context={context}")
            session_id = context["session_id"]

            msg_user_nickname = None
            if context.kwargs.get("msg").is_group:
                msg_user_nickname = context.kwargs.get("msg").actual_user_nickname
            else:
                msg_user_nickname = context.kwargs.get("msg").from_user_nickname
            welcome_msg = conf().get("welcome_msg_paipai", f'welcome {msg_user_nickname}')
            query = f'{welcome_msg}'
            logger.info(f"[RACIO-BOT] Build PATPAT query, query={query}, session_id={session_id}")
            
            reply = self._chat(query, context)
            return reply
        elif context.type == ContextType.ACCEPT_FRIEND:
            logger.info(f"[RACIO-BOT] Receive ACCEPT_FRIEND type msg, query={query}, context.type={context.type}, context={context}")
            session_id = context["session_id"]

            msg_user_nickname = 'Cool & Nice name!'    # '[nickname]'
            if context.kwargs.get("msg").is_group:
                msg_user_nickname = context.kwargs.get("msg").actual_user_nickname
            else:
                msg_user_nickname = context.kwargs.get("msg").from_user_nickname
            welcome_msg = conf().get("ACCEPT_FRIEND_PROMPT", f'你好 {msg_user_nickname}')
            query = f'{welcome_msg}'
            logger.info(f"[RACIO-BOT] Build ACCEPT_FRIEND query, query={query}, session_id={session_id}")

            reply = self._chat(query, context)
            return reply
        elif context.type == ContextType.JOIN_GROUP:
            logger.info(f"[RACIO-BOT] Receive JOIN_GROUP type msg, query={query}, context.type={context.type}, context={context}")
            session_id = context["session_id"]
            
            msg_user_nickname = context.kwargs.get("msg").actual_user_nickname
            group_welcome_msg = conf().get("group_welcome_msg", f'welcome {msg_user_nickname}')
            query = f'{group_welcome_msg}'
            logger.info(f"[RACIO-BOT] Build JOIN_GROUP query, query={query}, session_id={session_id}")
            
            reply = self._chat(query, context)
            return reply
        elif context.type == ContextType.EXIT_GROUP:
            logger.info(f"[RACIO-BOT] Receive EXIT_GROUP type msg, query={query}, context.type={context.type}, context={context}")
            return Reply(ReplyType.INFO, f"👋🏻👋🏻")
        elif context.type == ContextType.IMAGE_CREATE:
            ok, res = self.create_img(query, 0)
            if ok:
                reply = Reply(ReplyType.IMAGE_URL, res)
            else:
                reply = Reply(ReplyType.ERROR, res)
            return reply
        else:
            reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
            return reply

# get reply for query
    def _chat(self, query, context, retry_count=0) -> Reply:
        """
        发起对话请求
        :param query: 请求提示词
        :param context: 对话上下文
        :param retry_count: 当前递归重试次数
        :return: 回复
        """
        if retry_count >= 2:
            # exit from retry 2 times
            logger.warn("[RACIO-BOT] failed after maximum number of retry times")
            logger.warn(f'请再问我一次吧. (Error: [_chat]Failed after maximum number of retry times [{retry_count}])')
            return Reply(ReplyType.TEXT, f'我已尝试多次，目前线路依旧繁忙，请再问我一次吧.')

        try:
            # load config
            if context.get("generate_breaked_by"):
                logger.info(f"[RACIO-BOT] won't set appcode because a plugin ({context['generate_breaked_by']}) affected "
                            f"the context")
                app_code = None
            else:
                app_code = context.kwargs.get("app_code") or conf().get("racio_app_code")

            # api key
            racio_api_key = conf().get("racio_api_key", "racio_default_app_code")
            racio_user_id = conf().get("racio_user_id", "racio_default_user_id")

            # get user_id and user_nickname from context
            msg_user_id = None
            msg_user_nickname = None
            if context.kwargs.get("msg").is_group:
                msg_user_id = context.kwargs.get("msg").actual_user_id
                msg_user_nickname = context.kwargs.get("msg").actual_user_nickname
            else:
                msg_user_id = context.kwargs.get("msg").from_user_id
                msg_user_nickname = context.kwargs.get("msg").from_user_nickname

            # get session from context
            session_id = context["session_id"]
            session = self.sessions.session_query(query, session_id)

            # remove system message
            model = conf().get("model") or "gpt-3.5-turbo-16k"
            if session.messages[0].get("role") == "system":
                if app_code or model == "wenxin":
                    session.messages.pop(0)

            # Body of the request
            body = {
                "app_code": app_code,
                "messages": session.messages,
                "model": model,  # 对话模型的名称, 支持 gpt-3.5-turbo, gpt-3.5-turbo-16k, gpt-4, wenxin, xunfei
                
                # RACIO API payload (Optional) Provide user input fields as key-value pairs, corresponding to
                # variables in Prompt Eng. Key is the variable name, Value is the parameter value. If the field type
                # is Select, the submitted Value must be one of the preset choices.
                "inputs": {"user_nickname": msg_user_nickname, "user_id": msg_user_id},

                # User input/question content
                "query": query,
                
                # Blocking or streaming type
                # "response_mode": "blocking" or "streaming"
                # Blocking type, waiting for execution to complete and returning results.
                # (Requests may be interrupted if the process is long)
                # Streaming type, streaming returns.
                # (Implementation of streaming return based on SSE(Server-Sent Events) protocol).
                "response_mode": self.response_mode,

                # (Required) Conversation ID: ‼️ leave it empty for first-time (eg. conversation_id: "") conversation‼️
                # pass conversation_id from context to continue dialogue.
                "conversation_id": self._get_conversation_id(session_id),
                
                # The user identifier, defined by the developer, must ensure uniqueness within the app.
                "user": f"{racio_user_id}-{msg_user_nickname}" if msg_user_nickname is not None else f"{racio_user_id}-[nickName]"
            }

            # file
            file_id = context.kwargs.get("file_id")
            if file_id:
                body["file_id"] = file_id

            logger.info(f"[RACIO-BOT] query={query}, app_code={app_code}, mode={body.get('model')}, file_id={file_id}, "
                        f"user_id={body.get('user')}, inputs={body.get('inputs')}, conversation_id={body.get('conversation_id')}, response_mode={body.get('response_mode')}")

            # Header of the request
            headers = {"Authorization": "Bearer " + racio_api_key, "Content-Type": "application/json"}

            # do http request
            base_url = conf().get("racio_api_base", "https://at.racio.ai")
            res = requests.post(url=base_url + "/v1/chat-messages", json=body, headers=headers,
                                timeout=conf().get("request_timeout", 3600))
            if res.status_code == 200:
                if self.response_mode == 'streaming':
                    logger.info(f"[RACIO-BOT] response_mode: streaming")
                    return self._reply_streaming_message(msg_user_id, msg_user_nickname, res, session_id)
                if self.response_mode == 'blocking':
                    logger.info(f"[RACIO-BOT] response_mode: blocking")
                    return self._reply_blocking_message(msg_user_id, msg_user_nickname, res, session_id)

            else:
                logger.error(f"[RACIO-BOT] chat failed, status_code={res.status_code}, "
                             f"reason={res.reason}, content={res.content}")
                
                # handle error 400
                if res.status_code == 400:
                    # bad request
                    logger.error(f"[RACIO-BOT-400] chat failed, boy={body}, query={query}, session_id={session_id}, context={context}")
                    return Reply(ReplyType.INFO, f"😊")
                
                # handle error 404
                if res.status_code == 404:
                    # not found
                    logger.error(f"[RACIO-BOT-404] chat failed, boy={body}, query={query}, session_id={session_id}, context={context}")
                    return Reply(ReplyType.INFO, f"😊😊😊😊")
                                
                # handle error 500
                if res.status_code >= 500:
                    # server error, need retry
                    time.sleep(2)
                    logger.warn(f"[RACIO-BOT-50x] do retry, times={retry_count}")
                    return self._chat(query, context, retry_count + 1)

                # return Reply(ReplyType.ERROR, f"提问太快啦，请休息一下再问我吧.")
                return Reply(ReplyType.ERROR, f"提问太快啦，请休息一下再问我吧. status_code={res.status_code}, "
                             f"reason={res.reason}, content={res.content}")

        except Exception as e:
            logger.exception(e)
            # retry
            time.sleep(2)
            logger.warn(f"[RACIO-BOT] do retry, times={retry_count}")
            return self._chat(query, context, retry_count + 1)

    def _reply_blocking_message(self, msg_user_id, msg_user_nickname, res, session_id):
        # execute success
        response = res.json()

        reply_content = response["answer"]
        total_tokens = response["metadata"]

        conversation_id = response["conversation_id"]
        self._set_conversation_id(session_id, conversation_id)
        logger.info(
            f"[RACIO-BOT] reply={reply_content}, total_tokens={total_tokens}, "
            f"msg_user_id={msg_user_id}, msg_user_nickname={msg_user_nickname}, conversation_id={conversation_id}")
        self.sessions.session_reply(reply_content, session_id, total_tokens)

        agent_suffix = self._fetch_agent_suffix(response)
        if agent_suffix:
            reply_content += agent_suffix
        if not agent_suffix:
            knowledge_suffix = self._fetch_knowledge_search_suffix(response)
            if knowledge_suffix:
                reply_content += knowledge_suffix

        return Reply(ReplyType.TEXT_MULTI_LINE, reply_content)

    def _reply_streaming_message(self, msg_user_id, msg_user_nickname, response, session_id):
        # execute success

        data_id = ""
        task_id = ""
        message_id = ""
        thought = ""
        conversation_id = ""

        reply_content = ""
        total_tokens = ""

        for line in response.iter_lines(decode_unicode='utf-8'):
            if line:
                replaced_line = line.replace('data:', '"data":')
                json_str = f'{{ {replaced_line} }}'
                json_obj = json.loads(json_str)
                data = json_obj['data']

                if data['event'] == 'agent_thought' and data['thought']:
                    data_id = data['id']
                    task_id = data['task_id']
                    message_id = data['message_id']
                    thought = data['thought']
                    conversation_id = data['conversation_id']

                if data['event'] == 'message_end' and data['metadata'] and data['metadata']['usage']:
                    total_tokens = data['metadata']['usage']['total_tokens']

        reply_content = thought
        self._set_conversation_id(session_id, conversation_id)
        self.sessions.session_reply(reply_content, session_id, total_tokens)
        logger.info(
            f"[RACIO-BOT] reply={reply_content}, total_tokens={total_tokens}, "
            f"msg_user_id={msg_user_id}, msg_user_nickname={msg_user_nickname}, "
            f"conversation_id={conversation_id}")

        return Reply(ReplyType.TEXT_MULTI_LINE, reply_content)

    def reply_text(self, session: ChatGPTSession, app_code="", retry_count=0) -> dict:
        if retry_count >= 2:
            # exit from retry 2 times
            logger.warn("[RACIO-BOT] failed after maximum number of retry times")
            return {
                "total_tokens": 0,
                "completion_tokens": 0,
                "content": f'请再问我一次吧. (Error: [reply_text]Failed after maximum number of retry times [{retry_count}])'
            }

        try:
            body = {
                "app_code": app_code,
                "messages": session.messages,
                "model": conf().get("model") or "gpt-3.5-turbo",
                # 对话模型的名称, 支持 gpt-3.5-turbo, gpt-3.5-turbo-16k, gpt-4, wenxin, xunfei
                "temperature": conf().get("temperature"),
                "top_p": conf().get("top_p", 1),
                "frequency_penalty": conf().get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
                "presence_penalty": conf().get("presence_penalty", 0.0),  # [-2,2]之间，该值越大则更倾向于产生不同的内容
            }
            if self.args.get("max_tokens"):
                body["max_tokens"] = self.args.get("max_tokens")
            headers = {"Authorization": "Bearer " + conf().get("racio_api_key")}

            # do http request
            base_url = conf().get("racio_api_base", "https://at.racio.ai")
            res = requests.post(url=base_url + "/v1/chat-messages", json=body, headers=headers,
                                timeout=conf().get("request_timeout", 600))
            if res.status_code == 200:
                # execute success
                response = res.json()
                reply_content = response["choices"][0]["message"]["content"]
                total_tokens = response["usage"]["total_tokens"]
                logger.info(f"[RACIO-BOT] reply={reply_content}, total_tokens={total_tokens}")
                return {
                    "total_tokens": total_tokens,
                    "completion_tokens": response["usage"]["completion_tokens"],
                    "content": reply_content,
                }
            else:
                response = res.json()
                error = response.get("error")
                logger.error(f"[RACIO-BOT] chat failed, status_code={res.status_code}, "
                             f"msg={error.get('message')}, type={error.get('type')}")

                if res.status_code >= 500:
                    # server error, need retry
                    time.sleep(2)
                    logger.warn(f"[RACIO-BOT] do retry, times={retry_count}")
                    return self.reply_text(session, app_code, retry_count + 1)

                return {
                    "total_tokens": 0,
                    "completion_tokens": 0,
                    "content": f"提问太快啦，请休息一下再问我吧. (Error: chat failed, status_code={res.status_code}, reason={res.reason}, content={res.content})"
                }

        except Exception as e:
            logger.exception(e)
            # retry
            time.sleep(2)
            logger.warn(f"[RACIO-BOT] do retry, times={retry_count}")
            return self.reply_text(session, app_code, retry_count + 1)

    def _fetch_knowledge_search_suffix(self, response) -> str:
        try:
            if response.get("knowledge_base"):
                search_hit = response.get("knowledge_base").get("search_hit")
                first_similarity = response.get("knowledge_base").get("first_similarity")
                logger.info(f"[RACIO-BOT] knowledge base, search_hit={search_hit}, first_similarity={first_similarity}")
                plugin_config = pconf("racio")
                if plugin_config and plugin_config.get("knowledge_base") and plugin_config.get("knowledge_base").get(
                        "search_miss_text_enabled"):
                    search_miss_similarity = plugin_config.get("knowledge_base").get("search_miss_similarity")
                    search_miss_text = plugin_config.get("knowledge_base").get("search_miss_suffix")
                    if not search_hit:
                        return search_miss_text
                    if search_miss_similarity and float(search_miss_similarity) > first_similarity:
                        return search_miss_text
        except Exception as e:
            logger.exception(e)

    def _fetch_agent_suffix(self, response):
        try:
            plugin_list = []
            logger.debug(f"[LinkAgent] res={response}")
            if response.get("agent") and response.get("agent").get("chain") and response.get("agent").get(
                    "need_show_plugin"):
                chain = response.get("agent").get("chain")
                suffix = "\n\n- - - - - - - - - - - -"
                i = 0
                for turn in chain:
                    plugin_name = turn.get('plugin_name')
                    suffix += "\n"
                    need_show_thought = response.get("agent").get("need_show_thought")
                    if turn.get("thought") and plugin_name and need_show_thought:
                        suffix += f"{turn.get('thought')}\n"
                    if plugin_name:
                        plugin_list.append(turn.get('plugin_name'))
                        suffix += f"{turn.get('plugin_icon')} {turn.get('plugin_name')}"
                        if turn.get('plugin_input'):
                            suffix += f"：{turn.get('plugin_input')}"
                    if i < len(chain) - 1:
                        suffix += "\n"
                    i += 1
                logger.info(f"[LinkAgent] use plugins: {plugin_list}")
                return suffix
        except Exception as e:
            logger.exception(e)
