import random
import ujson as json

from .ttEncrypt import TTEncrypt
from common import log, Httpx

logger = log.log('device_register')


def random_device_type():
    string = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
    res = ''
    for i in range(3):
        res += random.choice(string)
    res += '-'
    for i in range(5):
        res += random.choice(string)

    return res


def random_udid():
    hex_str = list('abcdef0123456789')
    res = ''
    for i in range(40):
        res += random.choice(hex_str)
    return res


def register():
    dev = random_device_type()
    udid = random_udid()
    logger.info('正在注册设备\n' + f'device_type: {dev}\nopenudid: {udid}')
    data = json.dumps({
        'header': {
            'device_model': dev,
            'openudid': udid,
            'package': 'com.dragon.read'
        }
    })
    en = TTEncrypt(data)
    url = 'https://i.snssdk.com/service/2/device_register/'
    res = Httpx.request(url, {
        'method': 'POST',
        'body': en,
    })
    if (res.status_code == 200):
        res = json.loads(res.content)
        header = 'device_id=' + \
            res['device_id_str'] + '&device_type=' + \
            dev + '&iid=' + res['install_id_str']
        logger.info('设备注册成功！\n' + header)
        return header
    else:
        logger.warning('设备注册失败！')
        raise Exception('device register error') from None
