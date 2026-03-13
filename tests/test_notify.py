from notify import UrlPushNotify, UrlLoginQrCodeNotify

def test_push_notify() -> None:
    os.environ["PUSH_URL"] = "http://192.168.28.56:1880/sg/balanceNotify"
    url_notify = UrlPushNotify()
    assert url_notify is not None
    assert url_notify("test_user", 5.0) is True
    
def test_login_qrcode_notify() -> None:
    qrcode_notify = UrlLoginQrCodeNotify()
    assert qrcode_notify is not None
    # Simulate a QR code as bytes
    with open("assets/image-20230730135540291.png", 'rb') as f:
        binary_data = f.read()
    assert qrcode_notify(binary_data, "Test reason") is True