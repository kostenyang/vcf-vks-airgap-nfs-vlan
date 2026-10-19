"""vCenter root bash 指令(SSH → appliancesh → shell):python vc_shell.py "<cmd>" ["<cmd>" ...]"""
import paramiko, time, sys, re
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("vcf-m02-vc01.home.lab", username="root", password=__import__("os").environ["LABPW"], timeout=20, look_for_keys=False, allow_agent=False)
ch = c.invoke_shell(width=500, height=300); time.sleep(3); ch.recv(65536)
def send(cmd, wait=8):
    ch.send(cmd + "\n"); time.sleep(wait); out = b""
    while ch.recv_ready(): out += ch.recv(65536); time.sleep(0.3)
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", out.decode(errors="replace"))
send("shell", 4)
for cmd in sys.argv[1:]: print(send(cmd, 10))
