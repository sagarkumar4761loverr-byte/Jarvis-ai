[app]
title = JARVIS AI
package.name = jarvisai
package.domain = org.jarvis
source.dir = .
source.include_exts = py,png,jpg,kv,ttf,json
source.exclude_dirs = .github,bin,.buildozer,__pycache__
version = 1.0.0
requirements = python3,kivy==2.3.0,pyjnius,android,sqlite3,openssl,certifi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,RECORD_AUDIO,QUERY_ALL_PACKAGES,com.android.alarm.permission.SET_ALARM
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = False

[buildozer]
log_level = 2
warn_on_root = 1
