import subprocess
import sys
import os
import time
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import importlib
import json

def run_script(script_name, description):
    """运行指定的Python脚本"""
    print(f"\n{'='*50}")
    print(f"正在运行: {description}")
    print(f"{'='*50}")
    
    try:
        result = subprocess.run([sys.executable, script_name], 
                              capture_output=True, text=True, timeout=300)
        print(result.stdout)
        if result.stderr:
            print("错误信息:", result.stderr)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"运行 {script_name} 超时")
        return False
    except Exception as e:
        print(f"运行 {script_name} 失败: {e}")
        return False

def check_dependencies():
    """检查必要的依赖"""
    print("🔍 检查系统依赖...")
    print(f"Python路径: {sys.executable}")
    
    # 包名映射：导入名 -> 安装名
    package_mapping = {
        'chromadb': 'chromadb',
        'sentence_transformers': 'sentence-transformers',
        'fastapi': 'fastapi',
        'pypdf': 'pypdf',
        'openai': 'openai',
        'requests': 'requests',
        'sqlite3': 'sqlite3',  # 内置模块
        'uvicorn': 'uvicorn',
        'sentence_transformers': 'sentence-transformers'
    }
    
    missing_packages = []
    
    for import_name, install_name in package_mapping.items():
        try:
            if import_name == 'sqlite3':
                import sqlite3
                print(f"✅ {import_name} (内置)")
            else:
                # 尝试导入模块
                module = importlib.import_module(import_name)
                # 获取版本信息（如果可能）
                try:
                    version = getattr(module, '__version__', '未知版本')
                    print(f"✅ {import_name} ({install_name}) - 版本: {version}")
                except:
                    print(f"✅ {import_name} ({install_name})")
        except ImportError as e:
            missing_packages.append((import_name, install_name))
            print(f"❌ {import_name} - 需要安装: {install_name}")
            print(f"   错误详情: {e}")
    
    if missing_packages:
        print(f"\n❌ 缺少以下依赖:")
        for import_name, install_name in missing_packages:
            print(f"   - {import_name}: pip install {install_name}")
        
        print("\n💡 尝试自动安装缺失依赖...")
        for import_name, install_name in missing_packages:
            if install_name != 'sqlite3':  # sqlite3是内置的
                print(f"   正在安装 {install_name}...")
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", install_name])
                    print(f"   ✅ {install_name} 安装成功")
                    # 从缺失列表中移除
                    missing_packages = [p for p in missing_packages if p[0] != import_name]
                except subprocess.CalledProcessError:
                    print(f"   ❌ {install_name} 安装失败")
        
        # 重新检查是否还有缺失
        if missing_packages:
            print(f"\n⚠️  仍有依赖缺失，请手动安装:")
            for import_name, install_name in missing_packages:
                print(f"   pip install {install_name}")
            return False
    
    print("✅ 所有依赖检查通过")
    return True

def check_api_key():
    """检查API密钥配置"""
    try:
        # 检查环境变量中的API密钥配置
        print("🔍 检查API密钥配置...")
        api_key = os.environ.get('DEEPSEEK_API_KEY')
        
        # 如果环境变量中没有API密钥，则检查config.json文件
        if not api_key:
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    api_key = config.get('api_key')
            except FileNotFoundError:
                print("❌ 未找到 config.json 配置文件")
            except json.JSONDecodeError:
                print("❌ config.json 文件格式错误")
        
        if not api_key or api_key == "" or api_key == "sk-你的实际API密钥":
            print("⚠️  未设置 DEEPSEEK_API_KEY 环境变量或 config.json 中的 api_key")
            print("💡 请在环境变量中设置 DEEPSEEK_API_KEY 或在 config.json 中配置正确的 api_key")
            return False
        else:
            print("✅ DEEPSEEK_API_KEY 已配置")
        return True
    except Exception as e:
        print(f"⚠️  API密钥检查异常: {e}")
        return False

def start_web_server():
    """启动静态文件服务器"""
    print("\n🌐 启动静态文件服务器...")
    
    # 切换到当前目录
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # 检查index.html是否存在
    if not os.path.exists('index.html'):
        print("❌ 未找到 index.html 文件")
        return False
    
    # 启动HTTP服务器
    server = HTTPServer(('localhost', 8080), SimpleHTTPRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    
    print("✅ 静态文件服务器已在 http://localhost:8080 启动")
    return True

def main():
    """主函数"""
    print("🚀 启动智能招聘Agent系统...")
    print("=" * 60)
    
    # 检查依赖
    if not check_dependencies():
        print("❌ 依赖检查失败，系统无法启动")
        return False
    
    # 检查API密钥
    if not check_api_key():
        print("⚠️  API密钥检查失败")
    
    # 启动静态文件服务器
    if not start_web_server():
        print("❌ 静态文件服务器启动失败")
        return False
    
    # 启动AI服务
    print("\n🤖 启动AI服务...")
    try:
        # 使用subprocess启动ai_agent_server.py
        server_process = subprocess.Popen([sys.executable, 'ai_agent_server.py'])
        print("✅ AI服务启动成功")
        print(f"🌐 前端页面: http://localhost:8080")
        print(f"🔌 API接口: http://localhost:8000")
        print("\n💡 系统启动完成，按 Ctrl+C 停止服务")
        
        # 等待服务器进程结束
        server_process.wait()
        
    except KeyboardInterrupt:
        print("\n🛑 正在停止服务...")
        if 'server_process' in locals():
            server_process.terminate()
        print("✅ 服务已停止")
    except Exception as e:
        print(f"❌ 启动AI服务失败: {e}")
        return False
    
    return True

if __name__ == "__main__":
    main()