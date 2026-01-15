from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from Crypto.Util.number import bytes_to_long, long_to_bytes
import base64

class CM:
    def __init__(self):
        self.big_c = 2410312426921032588552076022197566074856950548502459942654116941958108831682612228890093858261341614673227141477904012196503648957050582631942730706805009223062734745341073406696246014589361659774041027169249453200378729434170325843778659198143763193776859869524088940195577346119843545301547043747207749969763750084308926339295559968882457872412993810129130294592999947926365264059284647209730384947211681434464714438488520940127459844288859336526896320919633919
        self.big_b = 2
        self.arry_d = base64.b64decode("rCXGfd2POMGzeiNIgo4iLg==")
        self.thisd = None
        self.thisc = None

    # 生成密钥对
    def pair(self):
        x = bytes_to_long(get_random_bytes(32)) % (self.big_c - 1)
        y = pow(self.big_b, x, self.big_c)
        self.thisd = (x, y)
        return self.thisd

    # 将公钥转为bytes
    def pair2arr(self, key_pair):
        _, public_key = key_pair
        return long_to_bytes(public_key)

    # 随机iv
    def rand(self):
        return get_random_bytes(16)

    # AES cbc
    def encode_a(self, key, iv, data):
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return cipher.encrypt(pad(data, AES.block_size))
    def encode_i(self, key, iv, data):
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(data), AES.block_size, style='pkcs7')

    def combine(self, arr1, arr2):
        return arr1 + arr2

    # 生成请求头y
    def client_handshake(self):
        key_pair = self.pair()
        self.thisc = self.rand()
        public_key_bytes = self.pair2arr(key_pair)
        a3 = self.encode_a(self.arry_d, self.thisc, public_key_bytes)
        combined = self.combine(self.thisc, a3)
        return base64.b64encode(combined).decode()

    def decrypt_f(self, peer_public_key, key_pair, iv, data):
        x, _ = key_pair
        shared_secret = pow(bytes_to_long(peer_public_key), x, self.big_c)
        key = long_to_bytes(shared_secret)[:32]
        return self.encode_i(key, iv, data)

    def decrypt(self, str1, str2, str3):
        if int(str1) == 1 and str2:
            decode = base64.b64decode(str3)
            iv = decode[:16]
            peer_public_key = base64.b64decode(str2)
            key_pair = self.thisd
            if key_pair is None:
                raise ValueError("Key pair not initialized")
            data = decode[16:]
            return self.decrypt_f(peer_public_key, key_pair, iv, data).decode('utf-8')
        return str3

if __name__ == '__main__':
    import requests
    ins = CM()
    resp = requests.get('https://novel.snssdk.com/api/novel/book/reader/content/v1/?device_platform=1&version_code=999&app_name=news_article&platform_id=1&item_id=7330095436042079269&aid=13&channel=HUAWEI&iid=0&device_type=1&os_version=1', headers={'user-agent': 'okhttp/3.13.3', 'y': ins.client_handshake()})
    j = resp.json()
    hy = resp.headers.get('y')
    print(ins.decrypt(1, hy, j['data']['content']))
