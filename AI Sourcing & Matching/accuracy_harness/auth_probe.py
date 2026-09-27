import sys, config, rcrm_auth
email, pw = sys.argv[1], sys.argv[2]
config.LOGIN_EMAIL = email; config.LOGIN_PASSWORD = pw
try:
    info = rcrm_auth.login()
    print("OK ->", info.get("account"), "| token len", len(info.get("token","")))
except Exception as e:
    print("FAIL ->", str(e)[:120])
