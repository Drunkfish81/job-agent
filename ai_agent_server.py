import io
import json
import time
import asyncio
import requests
import chromadb
import re
import sqlite3
from datetime import datetime
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import pypdf
from run_system import check_api_key
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ------------------------------------------------------------------


DATABASE_FILE = 'job_agent.db'

with open("config.json", "r") as f:
    config = json.load(f)

MY_DEEPSEEK_API_KEY = config.get("api_key")  # 请替换为你的真实密钥
# ------------------------------------------------------------------

def setup_database():
    """初始化数据库表"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # 搜索历史表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS match_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        resume_filename TEXT,
        keyword_used TEXT,
        salary_min INTEGER,
        salary_max INTEGER,
        jobs_count INTEGER,
        recommendation_text TEXT
    )
    ''')
    
    # 收藏岗位表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_name TEXT NOT NULL,
        salary TEXT,
        company_name TEXT,
        position_url TEXT UNIQUE,
        saved_at TEXT NOT NULL,
        notes TEXT
    )
    ''')
    
    # 对话会话表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        resume_text TEXT,
        matched_jobs_json TEXT,
        created_at TEXT NOT NULL
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
    ''')
    
    # 用户偏好表（新增）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        preference_type TEXT NOT NULL,
        preference_value TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, preference_type)
    )
    ''')
    
    conn.commit()
    conn.close()
    print("数据库初始化完成。")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

setup_database()

print("正在加载（向量搜索）AI 模型...")
MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
CHROMA_PATH = "chroma_db"
COLLECTION_NAME = "job_postings"
try:
    vector_model = SentenceTransformer(MODEL_NAME)
    client_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
    
    # 检查集合是否存在，如果不存在则创建
    try:
        collection = client_chroma.get_collection(name=COLLECTION_NAME)
        print("向量数据库和模型加载成功。")
    except Exception as e:
        print(f"向量数据库集合不存在: {e}")
        print("请先运行 vector_indexer.py 来创建向量数据库")
        collection = None
        
except Exception as e:
    print(f"加载向量数据库失败: {e}")
    client_chroma = None
    collection = None

print("正在配置 DeepSeek (简历分析) 客户端...")
if not MY_DEEPSEEK_API_KEY:
#or MY_DEEPSEEK_API_KEY != "": # 你的真实api
    print("!!!! 严重错误 !!!!")
    print("请设置正确的 DeepSeek API Key")
    client_deepseek = None
else:
    client_deepseek = OpenAI(
        api_key=MY_DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com"
    )
    print("DeepSeek 客户端配置成功。")

CRAWL_URL = "https://fe-api.zhaopin.com/c/i/search/positions"
CRAWL_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Cookie': 'x-zp-client-id=dc74f3ce-951b-4076-8da5-063699dbf6cf;'
}

def crawl_zhaopin(keyword: str):
    """爬取智联招聘"""
    print(f"--- 开始爬虫: keyword={keyword} ---")
    all_jobs_list = [] 
    for page_num in range(1, 11):
        payload = {
            "S_SOU_FULL_INDEX": keyword,
            "S_SOU_WORK_CITY": "489",
            "pageIndex": page_num,
            "pageSize": 20,
            "order": 4,
            "anonymous": 1,
            "eventScenario": "pcSearchedSouSearch",
            "platform": 13,
            "version": "0.0.0"
        }
        try:
            response = requests.post(CRAWL_URL, headers=CRAWL_HEADERS, json=payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                job_list = data.get('data', {}).get('list', [])
                if not job_list:
                    break 
                for job in job_list:
                    all_jobs_list.append({
                        "name": job.get('name'),
                        "salary": job.get('salary60'),
                        "companyName": job.get('companyName'),
                        "positionURL": job.get('positionURL')
                    })
            else:
                break
        except:
            break
        time.sleep(1) 
    print(f"--- 爬虫结束，共{len(all_jobs_list)}个岗位 ---")
    return all_jobs_list

def parse_salary(salary_str: str):
    if not salary_str or salary_str == "薪资面议":
        return (0, 999)
    numbers = re.findall(r'(\d+)', salary_str)
    if len(numbers) >= 2:
        return (int(numbers[0]), int(numbers[1]))
    elif len(numbers) == 1:
        num = int(numbers[0])
        return (num, num)
    return (0, 999)

def filter_by_salary(jobs: list, min_salary: int = None, max_salary: int = None):
    if not min_salary and not max_salary:
        return jobs
    filtered = []
    for job in jobs:
        salary_low, salary_high = parse_salary(job.get('salary', ''))
        if min_salary and salary_high < min_salary:
            continue
        if max_salary and salary_low > max_salary:
            continue
        filtered.append(job)
    return filtered

def get_user_preferences(user_id: str):
    """获取用户偏好"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT preference_type, preference_value FROM user_preferences WHERE user_id = ?', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        
        preferences = {}
        for row in rows:
            preferences[row['preference_type']] = row['preference_value']
        return preferences
    except:
        return {}

def save_user_preference(user_id: str, pref_type: str, pref_value: str):
    """保存用户偏好"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT OR REPLACE INTO user_preferences (user_id, preference_type, preference_value, created_at, updated_at)
        VALUES (?, ?, ?, datetime('now'), datetime('now'))
        ''', (user_id, pref_type, pref_value))
        conn.commit()
        conn.close()
        print(f"保存偏好: {pref_type} = {pref_value}")
        return True
    except Exception as e:
        print(f"保存偏好失败: {e}")
        return False

def extract_preferences_from_chat(message: str, user_id: str):
    """从聊天中提取并保存偏好"""
    message_lower = message.lower()
    
    # 薪资偏好
    if any(word in message_lower for word in ['薪资', '工资', '薪水', '高薪', '低了']):
        numbers = re.findall(r'(\d+)k', message_lower)
        if numbers:
            save_user_preference(user_id, 'min_salary', numbers[0])
    
    # 公司类型偏好
    if '大厂' in message or '一线' in message or 'bat' in message_lower:
        save_user_preference(user_id, 'company_type', '大厂')
    elif '外企' in message or '外资' in message:
        save_user_preference(user_id, 'company_type', '外企')
    elif '创业' in message or '初创' in message:
        save_user_preference(user_id, 'company_type', '创业公司')
    
    # 工作地点偏好
    cities = ['北京', '上海', '深圳', '广州', '杭州', '成都']
    for city in cities:
        if city in message:
            save_user_preference(user_id, 'location', city)
            break
    
    # 远程偏好
    if '远程' in message or '居家' in message or 'remote' in message_lower:
        save_user_preference(user_id, 'remote_work', '是')
    
    # 行业偏好
    industries = {
        'ai': 'AI人工智能', '互联网': '互联网', '游戏': '游戏',
        '金融': '金融', '教育': '教育', '医疗': '医疗'
    }
    for key, value in industries.items():
        if key in message_lower or key in message:
            save_user_preference(user_id, 'industry', value)
            break

APPLY_COOKIE = config.get("cookie")# 使用同一个有效 Cookie

def extract_position_id(url: str) -> str:
    match = re.search(r'jobs\.zhaopin\.com/([A-Z0-9]+J\d+)\.htm', url, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r'[?&]positionId=([A-Z0-9]+)', url, re.IGNORECASE)
    return match.group(1) if match else None

def auto_apply_to_job_browser(position_id: str, cookie_str: str) -> bool:
    """
    使用浏览器自动投递（增强版：支持多种按钮 + 调试截图）
    """
    options = Options()
    # options.add_argument("--headless=new")  # 调试时先注释掉，看浏览器操作
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    
    driver = webdriver.Chrome(options=options)
    try:
        # Step 1: 打开智联首页并注入 Cookie
        driver.get("https://www.zhaopin.com/")
        for cookie in parse_cookie_string(cookie_str):
            try:
                driver.add_cookie(cookie)
            except Exception as e:
                print(f"⚠️ 添加 Cookie 失败: {cookie['name']} - {e}")
        
        # Step 2: 访问岗位页面
        job_url = f"https://jobs.zhaopin.com/{position_id}.htm"
        driver.get(job_url)
        time.sleep(2)

        # === 【关键修改点：这里是新的点击逻辑】===
        print(f"🔍 当前页面标题: {driver.title}")
        print(f"🔗 当前 URL: {driver.current_url}")

        # 等待页面主体加载
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # 尝试多种可能的“申请”按钮选择器
        apply_selectors = [
            "//button[contains(text(), '申请')]",
            "//button[contains(text(), '投递')]",
            "//a[contains(text(), '申请')]",
            "//a[contains(text(), '投递')]",
            "//button[contains(@class, 'apply')]",
            "//div[contains(@class, 'apply-btn')]",
            "//button[@data-id='applyBtn']",
            "//span[contains(text(), '申请职位')]/ancestor::button",
            "//span[contains(text(), '立即投递')]/ancestor::button"
        ]

        applied = False
        for selector in apply_selectors:
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                print(f"✅ 找到可点击按钮: '{btn.text}' (选择器: {selector})")
                btn.click()
                time.sleep(2)
                
                # 检查是否出现“已申请”或成功提示
                try:
                    success_msg = driver.find_element(By.XPATH, "//*[contains(text(), '已申请') or contains(text(), '申请成功')]")
                    print("🎉 检测到申请成功提示")
                except:
                    pass
                    
                applied = True
                break
            except Exception as e:
                continue  # 尝试下一个选择器

        if not applied:
            print("❌ 所有申请按钮尝试失败，保存截图调试")
            driver.save_screenshot(f"debug_{position_id}.png")
            return False
        else:
            print(f"✅ 成功触发申请流程: {position_id}")
            return True

    except Exception as e:
        print(f"❌ 浏览器投递异常 ({position_id}): {e}")
        driver.save_screenshot(f"error_{position_id}.png")
        return False
    finally:
        driver.quit()

def parse_cookie_string(cookie_str: str):
    """将 Cookie 字符串转为 Selenium 可用的字典列表（带 domain/path）"""
    cookies = []
    for part in cookie_str.split(";"):
        if "=" in part:
            name = part.strip().split("=", 1)[0]
            value = part.strip().split("=", 1)[1]
            cookies.append({
                "name": name,
                "value": value,
                "domain": ".zhaopin.com",
                "path": "/"
            })
    return cookies

@app.get("/")
def read_root():
    return {"message": "你好！你的【V7.1 带记忆版】AI招聘Agent服务器正在运行。"}

@app.post("/analyze-resume")
async def analyze_resume_and_match(file: UploadFile = File(...), user_id: str = Form("default_user")):
    async def event_generator():
        if not client_deepseek:
            yield json.dumps({"error": "服务器端的 MY_DEEPSEEK_API_KEY 未配置，无法分析。"}) + "\n"
            return
        
        print(f"\n--- 收到简历 {file.filename}，用户ID: {user_id} ---")
        
        # 获取用户偏好
        user_prefs = get_user_preferences(user_id)
        print(f"用户偏好: {user_prefs}")
        
        # 步骤1: 读取 PDF
        yield json.dumps({"step": 1, "status": "active", "message": "正在读取简历..."}) + "\n"
        
        try:
            file_bytes = await file.read() 
            pdf_stream = io.BytesIO(file_bytes)
            reader = pypdf.PdfReader(pdf_stream)
            resume_text = ""
            for page in reader.pages:
                resume_text += page.extract_text()
            if not resume_text:
                yield json.dumps({"error": "无法从PDF中提取任何文本。"}) + "\n"
                return
            print(f"PDF 读取成功 ({len(resume_text)} 字符)。")
            yield json.dumps({"step": 1, "status": "completed", "message": "简历读取完成"}) + "\n"
        except Exception as e:
            yield json.dumps({"error": f"读取PDF失败: {e}"}) + "\n"
            return

        # 步骤2: AI 分析简历
        yield json.dumps({"step": 2, "status": "active", "message": "正在进行质量评分..."}) + "\n"
        
        prompt_task_1 = f"""
        你是资深HR。分析简历并提取信息：
        
        **任务1：关键词**（2-3个，空格分隔，如"C++ 算法"）
        **任务2：评分**（0-100分）
        **任务3：改进建议**（3-5条）
        **任务4：薪资建议**（最低-最高K/月）
        
        返回JSON：
        {{
          "searchKeyword": "关键词1 关键词2",
          "resume_score": 85,
          "score_breakdown": {{
            "content": "内容评价",
            "experience": "经验评价",
            "skills": "技能评价",
            "format": "格式评价"
          }},
          "improvements": ["建议1", "建议2", "建议3"],
          "salary_suggestion": {{
            "min": 15,
            "max": 22,
            "reasoning": "理由"
          }}
        }}

        简历：
        {resume_text}
        """
        
        print("正在AI分析...")
        try:
            response = client_deepseek.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt_task_1}],
                temperature=0.3
            )
            ai_response = response.choices[0].message.content.strip()
            if ai_response.startswith("```json"):
                ai_response = ai_response[7:]
            if ai_response.startswith("```"):
                ai_response = ai_response[3:]
            if ai_response.endswith("```"):
                ai_response = ai_response[:-3]
            ai_response = ai_response.strip()
            
            task_1_result = json.loads(ai_response)
            keyword = task_1_result.get("searchKeyword")
            resume_analysis = {
                "score": task_1_result.get("resume_score"),
                "breakdown": task_1_result.get("score_breakdown"),
                "improvements": task_1_result.get("improvements"),
                "salary": task_1_result.get("salary_suggestion")
            }
            
            # 返回步骤2的结果
            yield json.dumps({
                "step": 2, 
                "status": "completed", 
                "message": "质量评分完成",
                "data": {
                    "resume_analysis": {
                        "score": task_1_result.get("resume_score"),
                        "breakdown": task_1_result.get("score_breakdown"),
                    }
                }
            }) + "\n"
            
        except Exception as e:
            yield json.dumps({"error": f"AI分析失败: {e}"}) + "\n"
            return

        # 步骤3: 改进建议
        yield json.dumps({"step": 3, "status": "active", "message": "正在生成改进建议..."}) + "\n"
        
        # 简单延迟以模拟处理时间
        await asyncio.sleep(0.5)
        
        yield json.dumps({
            "step": 3, 
            "status": "completed", 
            "message": "改进建议生成完成",
            "data": {
                "improvements": task_1_result.get("improvements", [])
            }
        }) + "\n"

        # 步骤4: 推断薪资
        yield json.dumps({"step": 4, "status": "active", "message": "正在推断薪资..."}) + "\n"
        
        # 简单延迟以模拟处理时间
        await asyncio.sleep(0.5)
        
        yield json.dumps({
            "step": 4, 
            "status": "completed", 
            "message": "薪资推断完成",
            "data": {
                "salary": task_1_result.get("salary_suggestion", {})
            }
        }) + "\n"

        # === 爬取岗位 ===
        yield json.dumps({"step": 5, "status": "active", "message": "正在应用偏好..."}) + "\n"
        
        fresh_jobs = crawl_zhaopin(keyword)
        if not fresh_jobs:
            fresh_jobs = crawl_zhaopin("工程师")
        if not fresh_jobs:
            yield json.dumps({"error": "未找到岗位"}) + "\n"
            return

        # 应用用户偏好筛选
        min_sal = user_prefs.get('min_salary')
        if min_sal:
            min_sal = int(min_sal)
        else:
            min_sal = resume_analysis['salary']['min']
        
        max_sal = resume_analysis['salary']['max']
        
        filtered_jobs = filter_by_salary(fresh_jobs, min_sal, max_sal)
        if filtered_jobs:
            fresh_jobs = filtered_jobs
            salary_note = f"已根据你的偏好筛选{min_sal}-{max_sal}K薪资范围。"
        else:
            salary_note = f"未找到{min_sal}-{max_sal}K范围岗位。"

        # 返回步骤5的结果
        yield json.dumps({
            "step": 5, 
            "status": "completed", 
            "message": "偏好应用完成",
            "data": {
                "applied_preferences": user_prefs
            }
        }) + "\n"

        # === AI 匹配（带偏好） ===
        yield json.dumps({"step": 6, "status": "active", "message": "正在匹配岗位..."}) + "\n"
        
        jobs_text = json.dumps(fresh_jobs[:50], indent=2, ensure_ascii=False)
        
        # 构建偏好提示
        preference_prompt = ""
        if user_prefs:
            preference_prompt = f"""
    **用户偏好（必须优先考虑）：**
    """
            if user_prefs.get('company_type'):
                preference_prompt += f"- 公司类型：{user_prefs['company_type']}\n"
            if user_prefs.get('min_salary'):
                preference_prompt += f"- 最低薪资：{user_prefs['min_salary']}K\n"
            if user_prefs.get('location'):
                preference_prompt += f"- 工作地点：{user_prefs['location']}\n"
            if user_prefs.get('remote_work'):
                preference_prompt += f"- 远程工作：需要\n"
            if user_prefs.get('industry'):
                preference_prompt += f"- 行业：{user_prefs['industry']}\n"
        
        prompt_task_2 = f"""
        你是职业规划师。为求职者推荐3个最佳岗位。
        
        {salary_note}
        {preference_prompt}
        
        简历摘要：
        {resume_text[:1000]}
        
        岗位列表：
        {jobs_text}
        
        返回JSON：
        {{
          "recommendation_text": "开场白",
          "top_jobs": [
            {{
              "name": "岗位名",
              "salary": "薪资",
              "companyName": "公司",
              "positionURL": "链接",
              "analysis": {{
                "match_score": "★★★★★ 92%",
                "match_reason": "匹配分析（50字）",
                "salary_analysis": "薪资分析（30字）",
                "industry_outlook": "行业前景（30字）",
                "recommendation": "推荐理由（30字）",
                "skill_gap": {{
                  "matched_skills": ["技能1", "技能2"],
                  "missing_skills": ["缺失技能1"],
                  "learning_priority": "学习建议（40字）"
                }}
              }}
            }}
          ]
        }}
        
        注意：优先推荐符合用户偏好的岗位！
        """
        
        try:
            response = client_deepseek.chat.completions.create(
                model="deepseek-chat", 
                messages=[{"role": "user", "content": prompt_task_2}],
                temperature=0.5 
            )
            
            ai_response = response.choices[0].message.content.strip()
            if ai_response.startswith("```json"):
                ai_response = ai_response[7:]
            if ai_response.startswith("```"):
                ai_response = ai_response[3:]
            if ai_response.endswith("```"):
                ai_response = ai_response[:-3]
            ai_response = ai_response.strip()
            
            result_json = json.loads(ai_response)
            top_jobs = result_json.get('top_jobs', [])
            
            # 保存历史
            try:
                conn = sqlite3.connect(DATABASE_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                INSERT INTO match_history (timestamp, resume_filename, keyword_used, salary_min, salary_max, jobs_count, recommendation_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    datetime.now().isoformat(),
                    file.filename,
                    keyword,
                    min_sal,
                    max_sal,
                    len(fresh_jobs),
                    result_json.get('recommendation_text', '')
                ))
                conn.commit()
                conn.close()
            except:
                pass
            
            # 自动收藏
            auto_favorited_count = 0
            if top_jobs:
                try:
                    conn = sqlite3.connect(DATABASE_FILE)
                    cursor = conn.cursor()
                    for job in top_jobs:
                        try:
                            cursor.execute('''
                            INSERT OR REPLACE INTO favorites (job_name, salary, company_name, position_url, saved_at, notes)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ''', (
                                job.get('name', ''),
                                job.get('salary', ''),
                                job.get('companyName', ''),
                                job.get('positionURL', ''),
                                datetime.now().isoformat(),
                                'AI自动推荐'
                            ))
                            auto_favorited_count += 1
                        except:
                            pass
                    conn.commit()
                    conn.close()
                except:
                    pass
            
            # 创建会话
            session_id = f"session_{int(time.time())}"
            try:
                conn = sqlite3.connect(DATABASE_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                INSERT INTO chat_sessions (session_id, user_id, resume_text, matched_jobs_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                ''', (session_id, user_id, resume_text, json.dumps(fresh_jobs, ensure_ascii=False), datetime.now().isoformat()))
                conn.commit()
                conn.close()
            except:
                pass
            
            # 返回步骤6的结果
            yield json.dumps({
                "step": 6, 
                "status": "completed", 
                "message": "岗位匹配完成",
                "data": {
                    "recommended_jobs": top_jobs,
                    "auto_favorited": auto_favorited_count,
                    "session_id": session_id
                }
            }) + "\n"
            
        except Exception as e:
            yield json.dumps({"error": f"AI匹配失败: {e}"}) + "\n"
            pass
        
        # 步骤7: 自动投递
        # yield json.dumps({"step": 7, "status": "active", "message": "开始自动投递..."}) + "\n"
        
        # # 简单延迟以模拟处理时间
        # await asyncio.sleep(1)
        
        # yield json.dumps({
        #     "step": 7, 
        #     "status": "completed", 
        #     "message": f"✅ 分析完成！已自动收藏{auto_favorited_count}个推荐岗位"
        # }) + "\n"

        # 步骤7: 自动投递
        yield json.dumps({"step": 7, "status": "active", "message": "开始自动投递..."}) + "\n"

        auto_applied_count = 0
        for job in top_jobs:
            pos_url = job.get('positionURL')
            pos_id = extract_position_id(pos_url)
            if pos_id:
                success = auto_apply_to_job_browser(pos_id, APPLY_COOKIE)
                if success:
                    auto_applied_count += 1
                yield json.dumps({"step": 7, "substep": "applying", "job": job['name'], "company": job['companyName'], "success": success}) + "\n"
            await asyncio.sleep(1)

        yield json.dumps({
            "step": 7,
            "status": "completed",
            "message": f"✅ 分析完成！已自动收藏{auto_favorited_count}个推荐岗位，已自动投递{auto_applied_count}个岗位"
        }) + "\n"

    
    return StreamingResponse(event_generator(), media_type="text/plain")

async def analyze_resume_and_match_xx(
    file: UploadFile = File(...),
    user_id: str = Form("default_user")
):
    pass

    
#     if not client_deepseek:
#         return {"error": "服务器端的 MY_DEEPSEEK_API_KEY 未配置，无法分析。"}
    
#     print(f"\n--- 收到简历 {file.filename}，用户ID: {user_id} ---")
    
#     # 获取用户偏好
#     user_prefs = get_user_preferences(user_id)
#     print(f"用户偏好: {user_prefs}")

#     # === 读取 PDF ===
#     try:
#         file_bytes = await file.read() 
#         pdf_stream = io.BytesIO(file_bytes)
#         reader = pypdf.PdfReader(pdf_stream)
#         resume_text = ""
#         for page in reader.pages:
#             resume_text += page.extract_text()
#         if not resume_text:
#             return {"error": "无法从PDF中提取任何文本。"}
#         print(f"PDF 读取成功 ({len(resume_text)} 字符)。")
#     except Exception as e:
#         return {"error": f"读取PDF失败: {e}"}

#     # === AI 分析简历 ===
#     prompt_task_1 = f"""
#     你是资深HR。分析简历并提取信息：
    
#     **任务1：关键词**（2-3个，空格分隔，如"C++ 算法"）
#     **任务2：评分**（0-100分）
#     **任务3：改进建议**（3-5条）
#     **任务4：薪资建议**（最低-最高K/月）
    
#     返回JSON：
#     {{
#       "searchKeyword": "关键词1 关键词2",
#       "resume_score": 85,
#       "score_breakdown": {{
#         "content": "内容评价",
#         "experience": "经验评价",
#         "skills": "技能评价",
#         "format": "格式评价"
#       }},
#       "improvements": ["建议1", "建议2", "建议3"],
#       "salary_suggestion": {{
#         "min": 15,
#         "max": 22,
#         "reasoning": "理由"
#       }}
#     }}

#     简历：
#     {resume_text}
#     """
    
#     print("正在AI分析...")
#     try:
#         response = client_deepseek.chat.completions.create(
#             model="deepseek-chat",
#             messages=[{"role": "user", "content": prompt_task_1}],
#             temperature=0.3
#         )
#         ai_response = response.choices[0].message.content.strip()
#         if ai_response.startswith("```json"):
#             ai_response = ai_response[7:]
#         if ai_response.startswith("```"):
#             ai_response = ai_response[3:]
#         if ai_response.endswith("```"):
#             ai_response = ai_response[:-3]
#         ai_response = ai_response.strip()
        
#         task_1_result = json.loads(ai_response)
#         keyword = task_1_result.get("searchKeyword")
#         resume_analysis = {
#             "score": task_1_result.get("resume_score"),
#             "breakdown": task_1_result.get("score_breakdown"),
#             "improvements": task_1_result.get("improvements"),
#             "salary": task_1_result.get("salary_suggestion")
#         }
        
#     except Exception as e:
#         return {"error": f"AI分析失败: {e}"}

#     # === 爬取岗位 ===
#     fresh_jobs = crawl_zhaopin(keyword)
#     if not fresh_jobs:
#         fresh_jobs = crawl_zhaopin("工程师")
#     if not fresh_jobs:
#         return {"error": "未找到岗位"}

#     # 应用用户偏好筛选
#     min_sal = user_prefs.get('min_salary')
#     if min_sal:
#         min_sal = int(min_sal)
#     else:
#         min_sal = resume_analysis['salary']['min']
    
#     max_sal = resume_analysis['salary']['max']
    
#     filtered_jobs = filter_by_salary(fresh_jobs, min_sal, max_sal)
#     if filtered_jobs:
#         fresh_jobs = filtered_jobs
#         salary_note = f"已根据你的偏好筛选{min_sal}-{max_sal}K薪资范围。"
#     else:
#         salary_note = f"未找到{min_sal}-{max_sal}K范围岗位。"

#     # === AI 匹配（带偏好） ===
#     jobs_text = json.dumps(fresh_jobs[:50], indent=2, ensure_ascii=False)
    
#     # 构建偏好提示
#     preference_prompt = ""
#     if user_prefs:
#         preference_prompt = f"""
# **用户偏好（必须优先考虑）：**
# """
#         if user_prefs.get('company_type'):
#             preference_prompt += f"- 公司类型：{user_prefs['company_type']}\n"
#         if user_prefs.get('min_salary'):
#             preference_prompt += f"- 最低薪资：{user_prefs['min_salary']}K\n"
#         if user_prefs.get('location'):
#             preference_prompt += f"- 工作地点：{user_prefs['location']}\n"
#         if user_prefs.get('remote_work'):
#             preference_prompt += f"- 远程工作：需要\n"
#         if user_prefs.get('industry'):
#             preference_prompt += f"- 行业：{user_prefs['industry']}\n"
    
#     prompt_task_2 = f"""
#     你是职业规划师。为求职者推荐3个最佳岗位。
    
#     {salary_note}
#     {preference_prompt}
    
#     简历摘要：
#     {resume_text[:1000]}
    
#     岗位列表：
#     {jobs_text}
    
#     返回JSON：
#     {{
#       "recommendation_text": "开场白",
#       "top_jobs": [
#         {{
#           "name": "岗位名",
#           "salary": "薪资",
#           "companyName": "公司",
#           "positionURL": "链接",
#           "analysis": {{
#             "match_score": "★★★★★ 92%",
#             "match_reason": "匹配分析（50字）",
#             "salary_analysis": "薪资分析（30字）",
#             "industry_outlook": "行业前景（30字）",
#             "recommendation": "推荐理由（30字）",
#             "skill_gap": {{
#               "matched_skills": ["技能1", "技能2"],
#               "missing_skills": ["缺失技能1"],
#               "learning_priority": "学习建议（40字）"
#             }}
#           }}
#         }}
#       ]
#     }}
    
#     注意：优先推荐符合用户偏好的岗位！
#     """
    
#     try:
#         response = client_deepseek.chat.completions.create(
#             model="deepseek-chat", 
#             messages=[{"role": "user", "content": prompt_task_2}],
#             temperature=0.5 
#         )
        
#         ai_response = response.choices[0].message.content.strip()
#         if ai_response.startswith("```json"):
#             ai_response = ai_response[7:]
#         if ai_response.startswith("```"):
#             ai_response = ai_response[3:]
#         if ai_response.endswith("```"):
#             ai_response = ai_response[:-3]
#         ai_response = ai_response.strip()
        
#         result_json = json.loads(ai_response)
#         top_jobs = result_json.get('top_jobs', [])
        
#         # 保存历史
#         try:
#             conn = sqlite3.connect(DATABASE_FILE)
#             cursor = conn.cursor()
#             cursor.execute('''
#             INSERT INTO match_history (timestamp, resume_filename, keyword_used, salary_min, salary_max, jobs_count, recommendation_text)
#             VALUES (?, ?, ?, ?, ?, ?, ?)
#             ''', (
#                 datetime.now().isoformat(),
#                 file.filename,
#                 keyword,
#                 min_sal,
#                 max_sal,
#                 len(fresh_jobs),
#                 result_json.get('recommendation_text', '')
#             ))
#             conn.commit()
#             conn.close()
#         except:
#             pass
        
#         # 自动收藏
#         auto_favorited_count = 0
#         if top_jobs:
#             try:
#                 conn = sqlite3.connect(DATABASE_FILE)
#                 cursor = conn.cursor()
#                 for job in top_jobs:
#                     try:
#                         cursor.execute('''
#                         INSERT OR REPLACE INTO favorites (job_name, salary, company_name, position_url, saved_at, notes)
#                         VALUES (?, ?, ?, ?, ?, ?)
#                         ''', (
#                             job.get('name', ''),
#                             job.get('salary', ''),
#                             job.get('companyName', ''),
#                             job.get('positionURL', ''),
#                             datetime.now().isoformat(),
#                             'AI自动推荐'
#                         ))
#                         auto_favorited_count += 1
#                     except:
#                         pass
#                 conn.commit()
#                 conn.close()
#             except:
#                 pass
        
#         # 创建会话
#         session_id = f"session_{int(time.time())}"
#         try:
#             conn = sqlite3.connect(DATABASE_FILE)
#             cursor = conn.cursor()
#             cursor.execute('''
#             INSERT INTO chat_sessions (session_id, user_id, resume_text, matched_jobs_json, created_at)
#             VALUES (?, ?, ?, ?, ?)
#             ''', (session_id, user_id, resume_text, json.dumps(fresh_jobs, ensure_ascii=False), datetime.now().isoformat()))
#             conn.commit()
#             conn.close()
#         except:
#             pass
        
#         return {
#             "success": True,
#             "session_id": session_id,
#             "user_id": user_id,
#             "resume_analysis": resume_analysis,
#             "recommendation_text": result_json.get('recommendation_text', ''),
#             "recommended_jobs": top_jobs,
#             "matched_jobs": fresh_jobs[:10],
#             "auto_favorited": auto_favorited_count,
#             "applied_preferences": user_prefs  # 返回应用的偏好
#         }
        
#     except Exception as e:
#         return {"error": f"AI匹配失败: {e}"}

@app.post("/chat")
async def chat_with_ai(
    session_id: str = Form(...),
    message: str = Form(...),
    user_id: str = Form("default_user")
):
    """多轮对话接口（带偏好提取）"""
    if not client_deepseek:
        return {"error": "AI服务未配置"}
    
    # 提取并保存偏好
    extract_preferences_from_chat(message, user_id)
    
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM chat_sessions WHERE session_id = ?', (session_id,))
        session = cursor.fetchone()
        
        if not session:
            return {"error": "会话不存在"}
        
        resume_text = session['resume_text']
        matched_jobs = json.loads(session['matched_jobs_json'])
        
        cursor.execute('SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY timestamp', (session_id,))
        history = cursor.fetchall()
        conn.close()
        
        # 获取用户偏好
        user_prefs = get_user_preferences(user_id)
        prefs_text = ""
        if user_prefs:
            prefs_text = "用户偏好：" + ", ".join([f"{k}={v}" for k, v in user_prefs.items()])
        
        messages = [
            {"role": "system", "content": f"""你是专业求职顾问。

用户简历摘要：{resume_text[:500]}
已匹配岗位数：{len(matched_jobs)}
{prefs_text}

任务：
1. 回答求职问题
2. 如果用户表达新偏好（如"想去大厂"），告诉用户"已记住"
3. 如果用户要求重新筛选，从matched_jobs中筛选
4. 回答<150字
"""}
        ]
        
        for h in history:
            messages.append({"role": h['role'], "content": h['content']})
        messages.append({"role": "user", "content": message})
        
        response = client_deepseek.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        ai_reply = response.choices[0].message.content
        
        # 如果检测到偏好，在回复中说明
        if any(word in message for word in ['大厂', '外企', '远程', '薪资']):
            ai_reply = f"✅ 已记住你的偏好！\n\n{ai_reply}\n\n💡 下次上传简历时，我会优先推荐符合这些偏好的岗位。"
        
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO chat_messages (session_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
        ''', (session_id, 'user', message, datetime.now().isoformat()))
        cursor.execute('''
        INSERT INTO chat_messages (session_id, role, content, timestamp)
        VALUES (?, ?, ?, ?)
        ''', (session_id, 'assistant', ai_reply, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return {"success": True, "reply": ai_reply}
        
    except Exception as e:
        return {"error": f"对话失败: {str(e)}"}

@app.get("/preferences/{user_id}")
def get_preferences(user_id: str):
    """获取用户偏好"""
    prefs = get_user_preferences(user_id)
    return {"success": True, "preferences": prefs}

@app.delete("/preferences/{user_id}")
def clear_preferences(user_id: str):
    """清除用户偏好"""
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_preferences WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "偏好已清除"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/history")
def get_history(limit: int = 10):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
        SELECT id, timestamp, resume_filename, keyword_used, salary_min, salary_max, jobs_count
        FROM match_history 
        ORDER BY id DESC 
        LIMIT ?
        ''', (limit,))
        records = cursor.fetchall()
        conn.close()
        
        history_list = []
        for r in records:
            history_list.append({
                'id': r['id'],
                'timestamp': r['timestamp'],
                'resume_filename': r['resume_filename'],
                'keyword_used': r['keyword_used'],
                'salary_range': f"{r['salary_min'] or '不限'}K-{r['salary_max'] or '不限'}K",
                'jobs_count': r['jobs_count']
            })
        
        return {"success": True, "history": history_list}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/favorite")
async def add_favorite(
    job_name: str = Form(...),
    salary: str = Form(...),
    company_name: str = Form(...),
    position_url: str = Form(...),
    notes: str = Form("")
):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT OR REPLACE INTO favorites (job_name, salary, company_name, position_url, saved_at, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (job_name, salary, company_name, position_url, datetime.now().isoformat(), notes))
        conn.commit()
        conn.close()
        return {"success": True, "message": "收藏成功！"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/favorites")
def get_favorites():
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM favorites ORDER BY id DESC')
        records = cursor.fetchall()
        conn.close()
        
        favorites_list = []
        for r in records:
            favorites_list.append({
                'id': r['id'],
                'job_name': r['job_name'],
                'salary': r['salary'],
                'company_name': r['company_name'],
                'position_url': r['position_url'],
                'saved_at': r['saved_at'],
                'notes': r['notes']
            })
        
        return {"success": True, "favorites": favorites_list}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.delete("/favorite/{favorite_id}")
def delete_favorite(favorite_id: int):
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM favorites WHERE id = ?', (favorite_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "已取消收藏"}
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    check_api_key()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
