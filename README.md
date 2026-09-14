# 校园网自动登录工具（华东交通大学 · Dr.COM 城市热点 eportal）

> **配置一次，以后 Windows 开机自动登录校园网；离开校园（比如回家）自动检测并安静退出，不影响正常上网。**

针对你截图里的那套认证系统写成：
**华东交通大学校园网络认证系统**（由广州热点软件科技股份有限公司提供，即 Dr.COM / 城市热点），
登录地址 `http://172.16.2.100/a70.htm?...`。

---

## 目录

- [1. 这个工具能做什么](#1-这个工具能做什么)
- [2. 两种登录引擎（重要，先看这个）](#2-两种登录引擎重要先看这个)
- [3. 文件结构](#3-文件结构)
- [4. 安装（3 步）](#4-安装3-步)
- [5. 第一次配置](#5-第一次配置)
- [6. 设置开机自启](#6-设置开机自启)
- [7. 常用命令](#7-常用命令)
- [8. 它是怎么工作的（真实门户逆向结论）](#8-它是怎么工作的真实门户逆向结论)
- [9. 配置项说明](#9-配置项说明)
- [10. 密码是怎么保存的](#10-密码是怎么保存的)
- [11. 给室友用 / 多用户](#11-给室友用--多用户)
- [12. 测试方法](#12-测试方法)
- [13. 推送到 Git](#13-推送到-git)
- [14. 常见问题与排错](#14-常见问题与排错)
- [15. 已知限制](#15-已知限制)
- [16. 免责声明](#16-免责声明)

---

## 1. 这个工具能做什么

完整流程（完全对应需求文档第十六条）：

```
Windows 登录
   ↓
start.bat 自动运行（放在"启动"文件夹里）
   ↓
login.py
   ↓
等网络连接（最多 60 秒，超时就放弃，绝不无限等）
   ↓
访问校园网认证地址 http://172.16.2.100/a70.htm
   ├── 访问不到        → 不是校园网（例如在家）→ 不开浏览器、不弹错误、直接退出
   ├── 已经在线        → 什么都不做，直接退出
   └── 要求登录        → 填账号 → 填密码 → 选运营商 → 点登录 → 确认成功 → 结束
```

特点：

- **不在代码里写死任何账号密码**（需求文档第六条）。
- **默认不开浏览器窗口**（HTTP 引擎），开机时不会闪一个 Edge 出来。
- 装了 Selenium 也能用真正的浏览器操作网页元素（需求文档指定的方案）。
- 密码用 **Windows DPAPI 加密**或直接存进 **Windows 凭据管理器**。
- 配置文件放在 `%LOCALAPPDATA%` 下，**根本不在 Git 仓库里**，不可能被误提交。

---

## 2. 两种登录引擎（重要，先看这个）

需求文档要求用 Selenium 操作网页元素。但这里有一个**死循环**：

> 校园网没登录的时候是上不了外网的 → `pip install selenium` 装不了 → 但没有 selenium 就登录不了。

所以本工具同时提供两个引擎，默认 `engine: "auto"`：

| 引擎 | 依赖 | 速度 | 会不会弹浏览器 | 什么时候用 |
|---|---|---|---|---|
| `http` | **零依赖**（只用 Python 标准库） | 约 1 秒 | 不会 | 默认先试这个 |
| `selenium` | `selenium` + Edge | 约 5–10 秒 | 会（可设无界面） | `http` 失败时自动回退 |

**为什么 HTTP 引擎不算"绕过网页"**：它提交的字段、顺序、目标地址，和浏览器点"登 录"时
那个隐藏表单 `f0` 提交的**完全一样**（见第 8 节的对照表）。它只是没渲染界面而已。

想强制走 Selenium（例如要录屏给老师看）：

```bat
python login.py --test --engine selenium
```

---

## 3. 文件结构

```
InternetSignUpScript/
├── login.py                  # 主程序：命令行入口 + 完整登录流程
├── drcom_portal.py           # Dr.COM 门户协议知识库（真实元素/字段/判定标记）
├── netcheck.py               # 等网络 + 探测是否在校园网 + HTTP 客户端
├── engine_http.py            # 零依赖 HTTP 登录引擎
├── engine_selenium.py        # Selenium 4 + Edge 登录引擎
├── config_store.py           # 配置读写 + 密码安全保存（keyring / DPAPI）
├── start.bat                 # 开机启动入口（自动找 Python，不假设它在 PATH 里）
├── start_debug.bat           # 调试用（保留窗口，能看到全部输出）
├── install_autostart.bat     # 一键把 start.bat 放进"启动"文件夹
├── uninstall_autostart.bat   # 取消开机自启
├── push_to_git.bat           # 一键 git add/commit/push（会用提示问远程仓库地址）
├── tools/autostart.ps1       # install/uninstall_autostart.bat 真正干活的脚本
├── requirements.txt          # 第三方依赖（全部可选）
├── requirements-dev.txt      # 测试用依赖（pytest）
├── config.example.json       # 配置示例（真正配置不在这里）
├── .gitignore                # 防止账号密码等敏感信息被提交
├── tests/                    # 测试：101 个用例，全部可离线运行
│   ├── mock_portal.py        #   一个假的 Dr.COM 门户（端到端集成测试用）
│   └── test_*.py
└── README.md
```

> 说明：需求文档里的目录名是 `CampusNetAutoLogin/`。因为本 Git 仓库本身
> （`InternetSignUpScript`）就是给这个工具用的，所以直接放在仓库根目录。

配置文件**不在**仓库里，它在：

```
%LOCALAPPDATA%\CampusNetAutoLogin\config.json     ← 配置
%LOCALAPPDATA%\CampusNetAutoLogin\login.log       ← 运行日志
```

---

## 4. 安装（3 步）

### 第 1 步：安装 Python

到 <https://www.python.org/downloads/> 下载 Python 3.8 或更高版本。
**安装时一定要勾选 `Add python.exe to PATH`。**

检查是否装好（Win + R → `cmd` → 回车）：

```bat
python --version
```

### 第 2 步：拿到这个项目

```bat
git clone <你的仓库地址>
cd InternetSignUpScript
```

或者直接把整个文件夹拷给室友（见第 11 节）。

### 第 3 步（可选）：安装第三方依赖

```bat
python -m pip install -r requirements.txt
```

> **这一步可以跳过。** 不装任何东西工具也能正常登录。
> 装了 `selenium` 会多一条"真实浏览器"的登录路径；装了 `keyring` 会把密码
> 存进 Windows 凭据管理器而不是本地文件。

---

## 5. 第一次配置

在项目目录里打开命令行，运行：

```bat
python login.py --setup
```

会看到：

```
==============================================
校园网自动登录初始化
==============================================
请输入校园网账号（学号/手机号）: 2021xxxxxxxx
请选择运营商：
  1. 中国移动（账号后缀 @cmcc）
  2. 中国联通（账号后缀 @unicom）
  3. 中国电信（账号后缀 @telecom）
请输入序号 1-3: 1
请输入校园网密码（输入时不显示）:
配置已保存到：C:\Users\你\AppData\Local\CampusNetAutoLogin\config.json
密码已用 Windows DPAPI 加密后保存在本机配置里（只有当前 Windows 用户能解开）。

正在执行第一次登录测试…
登录成功：门户返回成功页（Dr.COMWebLoginID_3.htm）（引擎 http）
```

看到"登录成功"就说明配好了。**以后不用再输入账号密码。**

---

## 6. 设置开机自启

需求文档第十五条要求：**只用 Windows 启动文件夹，不碰注册表。**

### 方法 A：一键脚本（推荐）

```bat
install_autostart.bat
```

它会创建一个快捷方式：
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CampusNetAutoLogin.lnk`

取消自启：

```bat
uninstall_autostart.bat
```

### 方法 B：手动

1. 按 `Win + R`，输入 `shell:startup`，回车（会打开"启动"文件夹）。
2. 右键 `start.bat` → **创建快捷方式**。
3. 把快捷方式拖进刚才打开的"启动"文件夹。

### start.bat 是怎么找到 Python 的？

需求文档说"不要假设 Python 一定安装在 PATH 中"，所以 `start.bat` 按顺序找：

1. 项目内的虚拟环境 `.venv\Scripts\pythonw.exe`
2. Python 启动器 `pyw -3` / `py -3`
3. PATH 里的 `pythonw.exe` / `python.exe`
4. 常见安装位置 `%LOCALAPPDATA%\Programs\Python\Python3*`、`C:\Python3*`、`%ProgramFiles%\Python3*`

优先用 `pythonw.exe`（无控制台窗口）。如果全都找不到，会把错误写进
`%LOCALAPPDATA%\CampusNetAutoLogin\start_error.log`（**不会 `pause`**，因为在开机时
没有窗口，`pause` 会让进程永远挂在那里）。

---

## 7. 常用命令

| 命令 | 作用 |
|---|---|
| `python login.py` | 正常流程：没配置就进初始化，有配置就直接登录 |
| `python login.py --setup` | 重新配置账号 / 密码 / 运营商，并立刻试登录一次 |
| `python login.py --test` | 手动测试自动登录（显示完整过程） |
| `python login.py --test --engine selenium` | 强制用 Edge + Selenium 走一遍（需求文档指定方案） |
| `python login.py --probe` | **诊断**：打印门户真实返回内容、运营商选项、接口地址 |
| `python login.py --status` | 看当前配置（不含密码）和是否在校园网 |
| `python login.py --forget` | 删除本机保存的密码 |
| `python login.py --headless` | Selenium 无界面运行 |
| `python login.py --wait 120` | 把等待网络的时间改成 120 秒 |
| `python login.py --non-interactive` | 绝不停下来提问（没有配置就直接退出并记日志） |
| `start_debug.bat` | 双击即 `--verbose` 并保留窗口，最适合排错 |
| `start_debug.bat --probe` | 同上，但只做诊断、绝不登录 |
| `python -m unittest discover -s tests` | 跑全部测试 |
| `push_to_git.bat` | 提交并推送到 Git 远程仓库 |

**退出码**：`0` = 成功 / 本来就在线 / 不在校园网（都算正常）；`1` = 尝试登录但失败；`2` = 配置或用法错误。

---

## 8. 它是怎么工作的（真实门户逆向结论）

> 需求文档第十八条要求"不要凭空猜测元素"。这里给出的**全部是实际从门户源码里读出来的**，
> 你可以用 `python login.py --probe` 自己复核。

页面的 `body` 是空的，界面由 JavaScript 动态拼出来，所以光看 `a70.htm` 看不到表单。
真正的模板在：

```
http://172.16.2.100:801/eportal/extern/test/ip/8/pc.js
```

你所在网段是 `10.52.0.0 ~ 10.53.255.255`（配置里叫 `student-副本2`，序号 8），
认证方式是 **1 = Portal 协议**。模板里的真实元素：

```html
<form name="f3" method="post" onsubmit="return ee(3)">
  <input type="text"     name="DDDDD" maxlength="16" placeholder="学号" autocomplete="off">
  <input type="password" name="upass" maxlength="16" placeholder="密码" autocomplete="off">
  <input type="submit"   name="0MKKey" value="登 录">
</form>

<select name="ISP_select">
  <option value="-1">选择运营商</option>
  <option value="@cmcc">中国移动</option>
  <option value="@unicom">中国联通</option>
  <option value="@telecom">中国电信</option>
</select>

<!-- 真正被提交的隐藏表单 -->
<form name="f0" method="post" action="">
  <input type="hidden" name="DDDDD" value="">   <!-- 账号 -->
  <input type="hidden" name="upass" value="">   <!-- 密码 -->
  <input type="hidden" name="R1" value="0">
  <input type="hidden" name="R2" value="0">
  <input type="hidden" name="R3" value="0">
  <input type="hidden" name="R6" value="0">     <!-- 手机=1，PC=0 -->
  <input type="hidden" name="para" value="00">
  <input type="hidden" name="0MKKey" value="123456">
  <input type="hidden" name="buttonClicked" value="">
  <input type="hidden" name="redirect_url" value="">
  <input type="hidden" name="err_flag" value="">
  <input type="hidden" name="username" value="">
  <input type="hidden" name="password" value="">
  <input type="hidden" name="user" value="">
  <input type="hidden" name="cmd" value="">
  <input type="hidden" name="Login" value="">
</form>
```

登录逻辑来自 `http://172.16.2.100/a41.js`：

```javascript
// ee() -> saveLoginForm() -> setISP() -> login_portal()
eeObj.accountPrefix = accountPrefix == 1 ? (getTermType() != 2 ? ',0,' : ',1,') : "";  // PC 用 ",0,"
document.f0.DDDDD.value = this.accountPrefix + this.form.DDDDD.value + this.tempAccountSuffix;
document.f0.upass.value = this.form.upass.value;
document.f0.action = "http://172.16.2.100:801/eportal/?c=ACSetting&a=Login&...&loginMethod=1";
document.f0.submit();
```

所以最终提交的**复合账号**形如：

```
,0,你的学号@cmcc        （中国移动）
,0,你的学号@unicom      （中国联通）
,0,你的学号@telecom     （中国电信）
```

**登录结果判定**（用一次假账号实测复核过）：

| 门户返回的页面标记 | 含义 |
|---|---|
| `<!--Dr.COMWebLoginID_3.htm-->` | ✅ 登录成功 / 已在线 |
| `<!--Dr.COMWebLoginID_2.htm-->` 且 `Msg=01;` `msga='';` | ❌ 账号或密码错误 |
| `<!--Dr.COMWebLoginID_0.htm-->` | 登录页，需要（重新）登录 |

因为门户把界面放在 JS 里，所以 `--probe` 读 `a70.htm` 时看不到 `<select>`，
它会自动改用本文件里记录的**真实选项映射**，并提示你去 `pc.js` 复核。

### 如果学校以后改了页面怎么办

1. 运行 `python login.py --probe`，看页面还能不能识别。
2. 在登录页按 `F12` 打开开发者工具 → 按 `Ctrl + Shift + C` → 点账号输入框，
   看 `<input>` 的 `name` 是不是 `DDDDD`；密码框是不是 `upass`；按钮是不是 `0MKKey`。
3. 把新的 HTML 贴给我/改 `drcom_portal.py` 里的常量即可，其它代码不用动。

---

## 9. 配置项说明

真正生效的配置在 `%LOCALAPPDATA%\CampusNetAutoLogin\config.json`，
完整字段见 `config.example.json`。最常改的几个：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `login_url` | 你截图里的完整地址 | 认证页面地址。主机名和端口都从这里取，所以 `http://127.0.0.1:8080/xxx` 这种地址也能直接用 |
| `portal_host` | `172.16.2.100` | URL 里没有主机名时的兜底值 |
| `eportal_port` | `801` | eportal 认证接口端口 |
| `autofill_client_ip` | `true` | 每次运行把 URL 里的 `wlanuserip`/`ip` 换成当前网卡 IP。**换宿舍/换网口后必须开着**，否则地址里的旧 IP 会让认证失败 |
| `account` | — | 学号 / 手机号 |
| `operator` | — | `中国移动` / `中国联通` / `中国电信` |
| `engine` | `auto` | `auto` / `http` / `selenium` |
| `wait_network_seconds` | `60` | 等待网络和校园网的总时长上限 |
| `retry_times` | `1` | 非"密码错误"类失败的额外重试次数 |
| `headless` | `false` | Selenium 是否无界面 |

---

## 10. 密码是怎么保存的

按需求文档第七条，优先级如下（`password_backend: "auto"` 时自动选最安全的那档）：

| 后端 | 条件 | 安全性 |
|---|---|---|
| `keyring` → **Windows 凭据管理器** | 装了 `keyring` | 最高，由系统保管 |
| `dpapi` → **Windows DPAPI 加密后存本地配置** | 默认（纯标准库，零依赖） | 高：密文只有**同一个 Windows 用户**能解开，拷给别人也读不出来 |
| `plain` → 明文 | 前两种都不可用时 | 低，运行时会**明确警告**你 |

不管用哪种：

- 密码**从不**写进 Python 源码。
- 配置目录在 `%LOCALAPPDATA%`，**不在 Git 仓库里**；写文件时还会用 `icacls` 把权限收紧到只有你自己。
- `.gitignore` 里额外忽略了 `config.json` 等，作为第二道保险（需求文档第七条第 4 点）。
- 日志里**从不**记录密码（有单元测试专门验证这一点）。

---

## 11. 给室友用 / 多用户

（需求文档第六条）

直接把整个文件夹拷给室友（或者让他 `git clone`），他只需要：

```bat
python login.py --setup
```

输入**他自己的**账号密码。因为配置存在每个人的
`%LOCALAPPDATA%\CampusNetAutoLogin\` 里，所以：

- 你的账号密码不会被拷给他；
- 他的配置也不会覆盖你的；
- DPAPI 加密的密码即使被拷走，在别人电脑上也解不开。

---

## 12. 测试方法

### 测试（101 个用例，全部离线可跑，不需要校园网）

```bat
python -m unittest discover -s tests -v
```

分五组：

| 测试文件 | 覆盖内容 |
|---|---|
| `test_drcom_portal.py` | 运营商 → 账号后缀映射（和门户 `select` 的 `value` 逐一比对）；隐藏表单 `f0` 字段集合与真实模板**完全一致**；认证接口 URL 构造（端口 801、`c=ACSetting`、`a=Login`…）；用**真实抓取的失败页**验证 `Msg=01 + msga=''` → "账号或密码错误"；IP 自动修正 |
| `test_config_store.py` | 配置读写往返、未知字段不丢、`to_public_dict()` 不泄露密码、plain 后端、**DPAPI 加解密往返（含中文）** |
| `test_netcheck.py` | gb2312 解码、等网络、探测校园网、**超时一定会返回、绝不无限等待** |
| `test_engine_http.py` | HTTP 引擎全部分支：密码错 / 成功 / 已在线 / 无法识别 / 门户不可达 / 提交失败；**密码明文永不进入日志** |
| `test_integration_mock_portal.py` | **端到端**：在一个假 Dr.COM 门户（`tests/mock_portal.py`）上跑完整的 `login.py`，验证提交的字段与真实门户一致、三种运营商后缀正确、"已在线"时不重复提交、"不在校园网"时安静退出且退出码为 0 |

### 手动测试

```bat
python login.py --probe     :: 看门户现在什么状态、运营商选项是什么
python login.py --test      :: 完整跑一遍自动登录
```

`--test` 如果显示"当前不在校园网环境，不做任何操作，退出" —— **这是正确的**，
说明你现在没连校园网（比如在家），程序按设计安静退出了。

### 真实门户联调记录

本工具已在本机对真实门户做过非破坏性验证：

- `--probe` 成功识别出"要求登录"、本机 IP `10.53.53.219`、认证接口地址；
- 用**故意编造的账号**跑完整流程，门户正确返回
  `Dr.COMWebLoginID_2.htm` + `Msg=01`，工具判定为"账号或密码错误"并返回退出码 1。

（用真实账号的登录成功路径需要你自己的账号密码，我没法替你验证。）

---

## 13. 推送到 Git

### ⚠️ 先看这条：本机当前推不上去

在这台电脑上实测过（2026-09-14，未登录校园网时）：

| 目标 | 结果 |
|---|---|
| `http://www.msftconnecttest.com/connecttest.txt` | ✅ 返回真实内容，能通 |
| `https://pypi.org` / `https://github.com` / `https://gitee.com` / `https://mirrors.tuna.tsinghua.edu.cn` | ❌ 全部 `基础连接已经关闭`（TLS 被校园网关重置） |
| `~/.ssh/id_rsa` / `id_ed25519` | ❌ 不存在 |

也就是说：**校园网认证之前，所有 HTTPS 都被掐断**，而 `git push` 走 HTTPS/SSH，
所以现在无论如何都推不上去。同一条链路也解释了为什么那三个 pip 源全装不上。

**解决办法（任选其一）：**

1. 先让本工具把校园网登上（顺便也就验证了工具本身）：

   ```bat
   python login.py --setup
   ```

   登录成功后 HTTPS 就通了，再执行下面的推送命令。

2. 用手机热点 / 家里网络，再推。

3. 如果学校要求二次验证或 MAC 绑定，本工具帮不上，请先手动登录校园网。

### 推送命令

**方式 A：一键脚本**

```bat
push_to_git.bat                                        :: 推到已配置的 origin
push_to_git.bat https://github.com/你的用户名/InternetSignUpScript.git
```

**方式 B：手动**

先到 GitHub / Gitee 网页上**新建一个空仓库**（不要勾选 "Add a README"，
否则会有冲突），然后：

```bat
git init
git add -A
git commit -m "feat: 校园网自动登录工具（Dr.COM eportal）"
git branch -M main
git remote add origin https://github.com/你的用户名/InternetSignUpScript.git
git push -u origin main
```

用 HTTPS 推送时，密码栏要填 **Personal Access Token**，不是账号密码。

### 推送前请确认这些文件没有被提交

```bat
git status
git ls-files | findstr /i "config.json .log"
```

应该**什么都搜不到** —— 因为：

- 真正的配置在 `%LOCALAPPDATA%\CampusNetAutoLogin\`，根本不在仓库里；
- `.gitignore` 里额外忽略了 `config.json` / `*.log` 等（需求文档第七条第 3、4 点）。

项目里只有一个 `config.example.json`（`!config.example.json` 白名单放行），
里面**没有任何真实账号密码**。

---

## 14. 常见问题与排错

**Q：开机后什么反应都没有，怎么知道它跑了没有？**

看日志：`%LOCALAPPDATA%\CampusNetAutoLogin\login.log`

**Q：提示"找不到 Python"。**

装 Python 时没勾 `Add python.exe to PATH`。重装并勾上，或者把
`C:\Users\你\AppData\Local\Programs\Python\Python3xx` 加进 PATH。

**Q：提示"当前不在校园网环境"，但我明明在学校。**

1. 先确认浏览器能打开 `http://172.16.2.100/a70.htm`。
2. **关掉 Clash / v2ray 等系统代理**。工具内部已经禁用代理，但如果代理改的是路由表/TUN 模式，仍会拦掉内网地址。
3. 用 `python login.py --probe` 看具体错误。

**Q：日志显示"账号或密码错误"，但我确定没输错。**

- 运营商选错了。移动/联通/电信三种后缀不能混，用 `--setup` 重选。
- 账号本身带了后缀（比如学号后面已经有 `@cmcc`）。本工具会自动拼接，不用你手填后缀。

**Q：`autofill_client_ip` 是干什么的？**

你截图里的地址带着 `wlanuserip=10.53.53.219`，那是你**当时**的 IP。
换了宿舍网口或 DHCP 重新分配后，这个值就过期了，认证会失败。
所以工具每次运行都会把 `wlanuserip` 和 `ip` 换成当前网卡的真实 IP。

**Q：想用 Selenium 但报错 "没有安装 selenium"。**

```bat
python -m pip install -r requirements.txt
```

注意：**先得能上网才能装**。所以第一次还是用默认的 `http` 引擎登录，
登录成功后再装 selenium。

**Q：Edge 启动失败。**

Selenium 4 自带的 Selenium Manager 需要联网下载 EdgeDriver。首次可能失败，
直接用 `python login.py --engine http` 即可（不需要任何浏览器驱动）。

**Q：会不会影响我在家里上网？**

不会。程序访问不到校园网认证地址时会**直接退出**，不开浏览器、不弹错误窗口、
不改任何网络设置（需求文档第九条）。

---

## 15. 已知限制

诚实说明：

1. **真实账号的"登录成功"路径没有在本机验证过** —— 我没有你的账号密码。
   已验证的部分：接口地址、复合账号拼接、失败判定、网络探测、配置与密码存储。
2. 门户界面完全由 JavaScript 生成，学校若更换模板（`pc.js`）或调整
   `ISP_select` 的选项值，需要按第 8 节最后一段更新常量。
3. 本工具只做"认证登录"。若学校还要求 MAC 绑定、二次验证、短信验证码，
   需要另外处理（`0MKKey1` 是短信登录按钮，本工具不使用）。
4. 运营商后缀 `@cmcc / @unicom / @telecom` 取自你所在网段当前的门户模板，
   不同校区/不同网段可能不同，用 `--probe` 可以核对。

---

## 16. 免责声明

本工具仅用于**自动化你自己账号的校园网登录**，不绕过任何计费或认证机制，
不破解密码，不修改网络设备。请遵守学校网络管理规定，不要把账号借给他人使用。
