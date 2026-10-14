"""Run a command as root on the VCF Installer / SDDC Manager (vcf user has restricted sudo; use su with the root password)."""
import os
import paramiko, sys, time, base64
def run_root(host, cmd, vcf_pw=os.environ["LAB_PASSWORD"], root_pw=os.environ["LAB_PASSWORD"], timeout=120):
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username="vcf", password=vcf_pw, timeout=15)
    b64 = base64.b64encode(cmd.encode()).decode()
    ch = c.get_transport().open_session(); ch.get_pty(); ch.settimeout(timeout)
    ch.exec_command(f"su - root -c 'echo {b64} | base64 -d | bash'")
    time.sleep(2); ch.send(root_pw + "\n")
    out = b""
    while True:
        try:
            d = ch.recv(65536)
            if not d: break
            out += d
        except Exception: break
    c.close()
    return out.decode(errors="replace").replace(root_pw, "****")
if __name__ == "__main__":
    print(run_root(sys.argv[1], sys.argv[2]))
