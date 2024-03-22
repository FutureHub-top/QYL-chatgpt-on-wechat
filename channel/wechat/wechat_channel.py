# encoding:utf-8

"""
wechat channel
"""

import io
import json
import os
import threading
import random
import asyncio
import time
from datetime import datetime

import requests

from bridge.context import *
from bridge.reply import *
from channel.chat_channel import ChatChannel
from channel.wechat.wechat_message import *
from common.expired_dict import ExpiredDict
from common.log import logger
from common.singleton import singleton
from common.time_check import time_checker
from config import conf, get_appdata_dir
from lib import itchat
from lib.itchat.content import *

# 定义存储消息的列表
messages = []
messages_dict = {}

# 定义最后一个消息到达的时间戳
last_message_timestamp = 0.0

wechat_single_chat_waiting_time_min = conf().get("wechat_single_chat_waiting_time_min", 5)
wechat_single_chat_waiting_time_max = conf().get("wechat_single_chat_waiting_time_max", 10)


def build_key_for_message_string(original_string):
    # 首先，找到第一个'@'符号的位置
    at_index = original_string.find('@')

    # 如果字符串中包含'@'，执行以下操作
    if at_index != -1:
        # 移除第一个'@'符号
        string_without_at = original_string[:at_index] + original_string[at_index + 1:]

        # 然后，获取前10位数字（如果字符串长度小于10，将获取所有数字）
        digits = string_without_at[:10]

        # 输出结果
        return digits  # 这将输出 "example123456789"
    else:
        return original_string[:10]


# 定义启动计时器的函数
def start_timer():
    try:
        # 定义处理消息的函数
        def process_messages():
            global last_message_timestamp
            # 获取当前时间
            current_time = time.time()
            # 计算自最后一个消息以来经过的时间
            time_since_last_message = current_time - last_message_timestamp

            # 如果超过 wechat_single_chat_waiting_time_max 秒，则处理所有消息
            if time_since_last_message > wechat_single_chat_waiting_time_max:
                logger.info(f"[message_timer][process_messages] Processing messages:")
                logger.info(f"[message_timer][process_messages] Max waiting time exceeded: {wechat_single_chat_waiting_time_max}")
                last_message_timestamp = 0.0  # 重置时间戳

                for key, value in messages_dict.items():
                    combined_text_message = value['Text']
                    message_create_time = value['CreateTime']
                    logger.info(f'[message_timer][process_messages] key: {key}')
                    logger.info(f'[message_timer][process_messages] combined_text_message: {combined_text_message}，message_create_time: {message_create_time}')

                logger.info(
                    f'[message_timer][process_messages] before pop item, messages_dict: {len(messages_dict)}')

                if len(messages_dict):
                    # just pop and handle the first item in messages_dict
                    first_message_key = next(iter(messages_dict))
                    logger.info(
                        f'[message_timer][process_messages] first_message_key: {first_message_key}')
                    first_message_value = messages_dict.pop(first_message_key)

                    # handle message text via itchat
                    chatMsg = WechatMessage(first_message_value, False)
                    logger.info(f'[message_timer][process_messages] WechatChannel().handle_single: {chatMsg}')

                    WechatChannel().handle_single(chatMsg)
                    logger.info(f'[message_timer][process_messages] after pop item, messages_dict: {len(messages_dict)}')

                    if len(messages_dict):
                        # new thread to handle other message in messages_dict
                        task_message_process_waiting_time = conf().get("task_message_process_waiting_time", 5)
                        logger.info(
                            f'[message_timer][process_messages][task_thread] processing message after {task_message_process_waiting_time} seconds.')
                        task_thread = threading.Timer(task_message_process_waiting_time, process_messages)
                        task_thread.start()

        # 使用线程延迟执行处理函数
        logger.info(f"[message_timer][timer_thread.start()] at {datetime.now()}")
        timer_thread = threading.Timer(wechat_single_chat_waiting_time_max, process_messages)
        timer_thread.start()
    except Exception as ex:
        logger.info(f"[message_timer] Exception: {ex}")
        return None
    finally:
        logger.info(f"[message_timer][Finally] at {datetime.now()}")


@itchat.msg_register([TEXT, VOICE, PICTURE, NOTE, ATTACHMENT, SHARING])
def handler_single_msg(msg):
    try:
        if msg["Type"] == TEXT:
            global last_message_timestamp
            # 更新最后一个消息的到达时间戳
            last_message_timestamp = time.time()

            # 将接收到的消息添加到列表中
            # messages.append(msg)
            # message_key = build_key_for_message_string(msg['FromUserName'])
            message_key = msg['FromUserName']
            if message_key in messages_dict.keys():
               messages_dict[message_key]['Text'] = f"{messages_dict[message_key]['Text']} {msg['Text']}"
            else:
                # messages_dict[msg['FromUserName']] = msg['Text']
                messages_dict[message_key] = msg

            # 打印当前接收到的消息
            logger.info(f"{datetime.now()} [last message timestamp] {last_message_timestamp} - Received message: {msg['FromUserName']} => {msg['Text']}")

            # 启动或重置 wechat_single_chat_waiting_time_max 秒计时器
            start_timer()
            return None
        else:
            cmsg = WechatMessage(msg, False)
    except NotImplementedError as e:
        logger.debug("[WX]single message {} skipped: {}".format(msg["MsgId"], e))
        return None
    # if chatMsg is not None:
    #     WechatChannel().handle_single(chatMsg)
    WechatChannel().handle_single(cmsg)
    return None


@itchat.msg_register([TEXT, VOICE, PICTURE, NOTE, ATTACHMENT, SHARING], isGroupChat=True)
def handler_group_msg(msg):
    try:
        cmsg = WechatMessage(msg, True)
    except NotImplementedError as e:
        logger.debug("[WX]group message {} skipped: {}".format(msg["MsgId"], e))
        return None
    WechatChannel().handle_group(cmsg)
    return None


def _check(func):
    def wrapper(self, cmsg: ChatMessage):
        msgId = cmsg.msg_id
        if msgId in self.receivedMsgs:
            logger.info("Wechat message {} already received, ignore".format(msgId))
            return
        self.receivedMsgs[msgId] = True
        create_time = cmsg.create_time  # 消息时间戳
        if conf().get("hot_reload") == True and int(create_time) < int(time.time()) - 60:  # 跳过1分钟前的历史消息
            logger.debug("[WX]history message {} skipped".format(msgId))
            return
        if cmsg.my_msg and not cmsg.is_group:
            logger.debug("[WX]my message {} skipped".format(msgId))
            return
        return func(self, cmsg)

    return wrapper


# 可用的二维码生成接口
# https://api.qrserver.com/v1/create-qr-code/?size=400×400&data=https://www.abc.com
# https://api.isoyu.com/qr/?m=1&e=L&p=20&url=https://www.abc.com
def qrCallback(uuid, status, qrcode):
    # logger.debug("qrCallback: {} {}".format(uuid,status))
    if status == "0":
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(qrcode))
            _thread = threading.Thread(target=img.show, args=("QRCode",))
            _thread.setDaemon(True)
            _thread.start()
        except Exception as e:
            pass

        import qrcode

        url = f"https://login.weixin.qq.com/l/{uuid}"

        qr_api1 = "https://api.isoyu.com/qr/?m=1&e=L&p=20&url={}".format(url)
        qr_api2 = "https://api.qrserver.com/v1/create-qr-code/?size=400×400&data={}".format(url)
        qr_api3 = "https://api.pwmqr.com/qrcode/create/?url={}".format(url)
        qr_api4 = "https://my.tv.sohu.com/user/a/wvideo/getQRCode.do?text={}".format(url)
        print("You can also scan QRCode in any website below:")
        print(qr_api3)
        print(qr_api4)
        print(qr_api2)
        print(qr_api1)

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)


@singleton
class WechatChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        self.receivedMsgs = ExpiredDict(60 * 60)

    def startup(self):
        itchat.instance.receivingRetryCount = 600  # 修改断线超时时间
        # login by scan QRCode
        hotReload = conf().get("hot_reload", False)
        status_path = os.path.join(get_appdata_dir(), "itchat.pkl")
        itchat.auto_login(
            enableCmdQR=2,
            hotReload=hotReload,
            statusStorageDir=status_path,
            qrCallback=qrCallback,
        )
        self.user_id = itchat.instance.storageClass.userName
        self.name = itchat.instance.storageClass.nickName
        logger.info("Wechat login success, user_id: {}, nickname: {}".format(self.user_id, self.name))

        # start message listener
        itchat.run()


    # handle_* 系列函数处理收到的消息后构造Context，然后传入produce函数中处理Context和发送回复
    # Context包含了消息的所有信息，包括以下属性
    #   type 消息类型, 包括TEXT、VOICE、IMAGE_CREATE
    #   content 消息内容，如果是TEXT类型，content就是文本内容，如果是VOICE类型，content就是语音文件名，如果是IMAGE_CREATE类型，content就是图片生成命令
    #   kwargs 附加参数字典，包含以下的key：
    #        session_id: 会话id
    #        isgroup: 是否是群聊
    #        receiver: 需要回复的对象
    #        msg: ChatMessage消息对象
    #        origin_ctype: 原始消息类型，语音转文字后，私聊时如果匹配前缀失败，会根据初始消息是否是语音来放宽触发规则
    #        desire_rtype: 希望回复类型，默认是文本回复，设置为ReplyType.VOICE是语音回复

    @time_checker
    @_check
    def handle_single(self, cmsg: ChatMessage):
        # filter system message
        if cmsg.other_user_id in ["weixin"]:
            return
        if cmsg.ctype == ContextType.VOICE:
            if conf().get("speech_recognition") != True:
                return
            logger.debug("[WX]receive voice msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[WX]receive image msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.PATPAT:
            logger.debug("[WX]receive patpat msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            logger.debug("[WX]receive text msg: {}, cmsg={}".format(json.dumps(cmsg._rawmsg, ensure_ascii=False), cmsg))
        else:
            logger.debug("[WX]receive msg: {}, cmsg={}".format(cmsg.content, cmsg))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=False, msg=cmsg)
        if context:
            self.produce(context)

    @time_checker
    @_check
    def handle_group(self, cmsg: ChatMessage):
        if cmsg.ctype == ContextType.VOICE:
            if conf().get("group_speech_recognition") != True:
                return
            logger.debug("[WX]receive voice for group msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.IMAGE:
            logger.debug("[WX]receive image for group msg: {}".format(cmsg.content))
        elif cmsg.ctype in [ContextType.JOIN_GROUP, ContextType.PATPAT, ContextType.ACCEPT_FRIEND,
                            ContextType.EXIT_GROUP]:
            logger.debug("[WX]receive note msg: {}".format(cmsg.content))
        elif cmsg.ctype == ContextType.TEXT:
            # logger.debug("[WX]receive group msg: {}, cmsg={}".format(json.dumps(cmsg._rawmsg, ensure_ascii=False), cmsg))
            pass
        elif cmsg.ctype == ContextType.FILE:
            logger.debug(f"[WX]receive attachment msg, file_name={cmsg.content}")
        else:
            logger.debug("[WX]receive group msg: {}".format(cmsg.content))
        context = self._compose_context(cmsg.ctype, cmsg.content, isgroup=True, msg=cmsg)
        if context:
            self.produce(context)

    # 统一的发送函数，每个Channel自行实现，根据reply的type字段发送不同类型的消息
    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        if reply.type == ReplyType.TEXT:
            itchat.send(reply.content, toUserName=receiver)
            logger.info("[WX] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.TEXT_MULTI_LINE:
            reply_list = re.split(r'[。]', reply.content.strip())
            delay_count = len(reply_list) - 1
            wechat_response_random_min = conf().get("wechat_response_random_min", 1)
            wechat_response_random_max = conf().get("wechat_response_random_max", 10)
            for reply in reply_list:
                logger.info("[WX] sendMsg={}, receiver={}".format(reply, receiver))
                reply = ''.join(reply.splitlines()).strip()
                random.seed(datetime.now().timestamp())
                if reply != '':
                    # reply message
                    itchat.send(reply, toUserName=receiver)
                    # sleep a while before reply
                    if delay_count > 0:
                        delay_time = random.randint(wechat_response_random_min, wechat_response_random_max)
                        logger.info("[WX] reply delay={} seconds".format(delay_time))
                        time.sleep(delay_time)
                        delay_count -= 1
        elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
            itchat.send(reply.content, toUserName=receiver)
            logger.info("[WX] sendMsg={}, receiver={}".format(reply, receiver))
        elif reply.type == ReplyType.VOICE:
            itchat.send_file(reply.content, toUserName=receiver)
            logger.info("[WX] sendFile={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL:  # 从网络下载图片
            img_url = reply.content
            logger.debug(f"[WX] start download image, img_url={img_url}")
            pic_res = requests.get(img_url, stream=True)
            image_storage = io.BytesIO()
            size = 0
            for block in pic_res.iter_content(1024):
                size += len(block)
                image_storage.write(block)
            logger.info(f"[WX] download image success, size={size}, img_url={img_url}")
            image_storage.seek(0)
            itchat.send_image(image_storage, toUserName=receiver)
            logger.info("[WX] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
            image_storage = reply.content
            image_storage.seek(0)
            itchat.send_image(image_storage, toUserName=receiver)
            logger.info("[WX] sendImage, receiver={}".format(receiver))
        elif reply.type == ReplyType.FILE:  # 新增文件回复类型
            file_storage = reply.content
            itchat.send_file(file_storage, toUserName=receiver)
            logger.info("[WX] sendFile, receiver={}".format(receiver))
        elif reply.type == ReplyType.VIDEO:  # 新增视频回复类型
            video_storage = reply.content
            itchat.send_video(video_storage, toUserName=receiver)
            logger.info("[WX] sendFile, receiver={}".format(receiver))
        elif reply.type == ReplyType.VIDEO_URL:  # 新增视频URL回复类型
            video_url = reply.content
            logger.debug(f"[WX] start download video, video_url={video_url}")
            video_res = requests.get(video_url, stream=True)
            video_storage = io.BytesIO()
            size = 0
            for block in video_res.iter_content(1024):
                size += len(block)
                video_storage.write(block)
            logger.info(f"[WX] download video success, size={size}, video_url={video_url}")
            video_storage.seek(0)
            itchat.send_video(video_storage, toUserName=receiver)
            logger.info("[WX] sendVideo url={}, receiver={}".format(video_url, receiver))
