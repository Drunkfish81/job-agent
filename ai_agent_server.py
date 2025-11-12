import os
import io
import json 
import requests 
import chromadb
import time
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
import pypdf

# ------------------------------------------------------------------
# (危险的硬编码 Key，来自上一步)
MY_DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" # 确保这里是你的 Key
# ------------------------------------------------------------------

# --- 1. FastAPI 应用 和 CORS ---
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. 向量搜索 (旧功能) ---
print("正在加载（向量搜索）AI 模型...")
MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
CHROMA_PATH = "chroma_db"
COLLECTION_NAME = "job_postings"
try:
    vector_model = SentenceTransformer(MODEL_NAME)
    client_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client_chroma.get_collection(name=COLLECTION_NAME)
    print("向量数据库和模型加载成功。")
except Exception as e:
    print(f"加载向量数据库失败: {e}")
    print("警告：/search 接口将无法工作。")

# --- 3. DeepSeek AI (新功能) ---
print("正在配置 DeepSeek (简历分析) 客户端...")
if not MY_DEEPSEEK_API_KEY or MY_DEEPSEEK_API_KEY == "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx":
    print("!!!! 严重错误 !!!!")
    print("你没有在代码顶部的 MY_DEEPSEEK_API_KEY 变量中填入你的 Key！")
    client_deepseek = None
else:
    client_deepseek = OpenAI(
        api_key=MY_DEEPSEEK_API_KEY, 
        base_url="https://api.deepseek.com"
    )
    print("DeepSeek 客户端配置成功。")

# --- 4. (修改) 实时爬虫函数 ---
# --- 4. (修改) 实时爬虫函数 (10页循环版) ---
CRAWL_URL = "https://fe-api.zhaopin.com/c/i/search/positions"
CRAWL_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 ',
    'Cookie': 'x-zp-client-id=dc74f3ce-951b-4076-8da5-063699dbf6cf; sajssdk_2015_cross_new_user=1; sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219a78024897e0-0bbfbc937d5dc3-26011b51-1821369-19a78024898247%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E7%9B%B4%E6_E%A5%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC_%E7%9B%B4%E6%8E%A5%E6%89%93%E5%BC%80%22%2C%22%24latest_referrer%22%3A%22%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTlhNzgwMjQ4OTdlMC0wYmJmYmM5MzdkNWRjMy0yNjAxMWI1MS0xODIxMzY5LTE5YTc4MDI0ODk4MjQ3In0%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219a78024897e0-0bbfbc937d5dc3-26011b51-1821369-19a78024898247%22%7D; sensorsdata2015jssdkchannel=%7B%22prop%E%7B%22_sa_channel_landing_url%22%3A%22%22%7D%7D; Hm_lvt_7fa4effa4233f03d11c7e2c710749600=1762950011; HMACCOUNT=8969172F52244AB1; Hm_lpvt_7fa4effa4233f03d11c7e2c710749600=1762950020; LastCity=undefined; LastCity%5Fid=undefined' # (如果爬虫失败, 记得更新这个!)
}

def crawl_zhaopin(keyword: str):
    """
    根据AI分析的关键词，循环爬取智联招聘(全国) 1到10页。
    """
    print(f"--- 开始实时爬虫: keyword={keyword} (全国) ---")
    
    # 准备一个“大篮子”装所有岗
    all_jobs_list = [] 

    # range(1, 11) 会循环 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
    for page_num in range(1, 11):
        print(f"正在爬取第 {page_num} 页...")
        
        
        payload = {
            "S_SOU_FULL_INDEX": keyword,  # 搜索关键词 (S_SOU = "搜")，由 DeepSeek 从简历中提取
            "S_SOU_WORK_CITY": "489", # 工作城市代码 "489" 是智联招聘内部代表“北京”的代码，但在这里也泛指“全国”或“默认”
            "pageIndex": page_num, # <--- 关键：使用当前的循环页码
            "pageSize": 20, # 每页返回的数据条数，即你希望一次获取多少个岗位。
            "order": 4,# 排序方式。4 可能是网站默认的“智能排序”或“相关性排序”。(0 或 1 可能是“最新发布”)
            "anonymous": 1, # 是否匿名请求。1 代表 True (是)，表示这是一个未登录用户的请求。
            "eventScenario": "pcSearchedSouSearch", # 这是一个给服务器看的数据分析字段，告诉它这个请求是“来自PC端的搜索框”。
            "platform": 13, # 13 很可能是代表“PC Web端”的内部ID，用来和App端区分
            "version": "0.0.0" # API版本号，这里似乎只是一个占位符。
        }

        try:
            response = requests.post(CRAWL_URL, headers=CRAWL_HEADERS, json=payload, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                job_list = data.get('data', {}).get('list', [])
                
                if not job_list:
                    # 如果这一页是空的，说明后面也没了，直接“收工”
                    print(f"第 {page_num} 页没有数据，停止爬取。")
                    break 
                
                print(f"第 {page_num} 页成功找到 {len(job_list)} 个岗位。")
                
                # 简化数据并装进“小篮子”
                simplified_list = []
                for job in job_list:
                    simplified_list.append({
                        "name": job.get('name'),
                        "salary": job.get('salary60'),
                        "companyName": job.get('companyName'),
                        "positionURL": job.get('positionURL')
                    })
                
                # 把“小篮子”里的货倒进“大篮子”
                all_jobs_list.extend(simplified_list)
            
            else:
                # 如果某一页失败了 (比如被封了), 咱就别继续了
                print(f"爬取第 {page_num} 页失败, HTTP状态码: {response.status_code}")
                break # 停止循环

        except requests.RequestException as e:
            print(f"爬取第 {page_num} 页网络请求出错: {e}")
            break # 停止循环
        
        # --- 关键：礼貌 ---
        # 每次请求后“假装”休息1秒钟，别把人家服务器搞崩了
        time.sleep(1) 

    # 循环结束后，返回“大篮子”里所有的货
    print(f"--- 爬虫结束，总共收集到 {len(all_jobs_list)} 个岗位 ---")
    return all_jobs_list

# --- 5. 接口 (根目录 和 搜索) ---
@app.get("/")
def read_root():
    return {"message": "你好！你的【V4.2 全国版】AI招聘Agent服务器正在运行。"}

@app.get("/search")
def search_jobs(keyword: str):
    if not keyword: return {"error": "请输入 'keyword' 参数"}
    jobs = query_jobs_from_vector_db(keyword, top_n=10)
    return {"keyword": keyword, "results": jobs}

# --- 6. 接口: "/analyze-resume" (!!! 简化 !!!) ---
@app.post("/analyze-resume")
async def analyze_resume_and_match(file: UploadFile = File(...)):
    
    if not client_deepseek:
        return {"error": "服务器端的 MY_DEEPSEEK_API_KEY 未配置，无法分析。"}
        
    print(f"\n--- 收到简历 {file.filename}，开始“一条龙”服务 ---")

    # === 阶段 1: 读取 PDF ===
    try:
        file_bytes = await file.read() 
        pdf_stream = io.BytesIO(file_bytes)
        reader = pypdf.PdfReader(pdf_stream)
        resume_text = ""
        for page in reader.pages:
            resume_text += page.extract_text()
            
        if not resume_text:
            print("错误：PDF中没有提取到文本。")
            return {"error": "无法从PDF中提取任何文本。"}
        print(f"阶段 1/4: PDF 读取成功 ({len(resume_text)} 字符)。")

    except Exception as e:
        print(f"读取 PDF 失败: {e}")
        return {"error": f"读取PDF文件失败: {e}"}

    # === 阶段 2: AI 任务 1 (解析简历) ===
    
    # !!! --- 更改 3: 简化了AI的指令 --- !!!
    prompt_task_1 = f"""
    你是一个专业的HR助理。请仔细阅读以下简历文本，并提取关键信息。
    你必须严格按照以下 JSON 格式返回，不要添加任何额外的解释：
    {{
      "searchKeyword": "用于招聘网站搜索的核心关键词 (例如: Java后端, Python数据分析)"
    }}

    --- 简历文本如下 ---
    {resume_text}
    """
    print("阶段 2/4: 正在调用 DeepSeek (任务1：解析关键词)...")
    try:
        response = client_deepseek.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": "你是一个有用的助手，严格按JSON格式输出。"}, {"role": "user", "content": prompt_task_1}],
            temperature=0.0
        )
        ai_response_content = response.choices[0].message.content
        
        # !!! --- 更改 4: 只解析 keyword --- !!!
        task_1_json = json.loads(ai_response_content)
        keyword = task_1_json.get("searchKeyword")
        
        if not keyword:
            print(f"AI任务1返回的JSON不完整: {ai_response_content}")
            return {"error": "AI未能成功提取搜索关键词。"}
            
        print(f"DeepSeek 任务1完成: 关键词={keyword}")

    except Exception as e:
        print(f"调用 DeepSeek API (任务1) 失败: {e}")
        return {"error": f"调用 DeepSeek API (任务1) 失败: {e}"}

    # === 阶段 3: 实时爬虫 ===
    print(f"阶段 3/4: 正在执行实时爬虫...")
    
    # !!! --- 更改 5: 调用爬虫时不再传入 city_code --- !!!
    fresh_jobs = crawl_zhaopin(keyword)
    
    if not fresh_jobs:
        print("爬虫没有抓到任何岗位。")
        return {"error": f"爬虫未能找到关于 '{keyword}' 的任何岗位，请稍后再试或检查Cookie。"}

    # === 阶段 4: AI 任务 2 (简历 + 岗位 匹配) ===
    # (这部分逻辑保持不变)
    jobs_text = json.dumps(fresh_jobs, indent=2, ensure_ascii=False)
    
    prompt_task_2 = f"""
    你是一个顶级的职业规划师和HR专家。你的任务是帮助我匹配最合适的岗位。
    
    这是我的简历：
    --- [我的简历] ---
    {resume_text}
    --- [简历结束] ---
    
    这是我刚刚为你爬到的最新岗位列表：
    --- [岗位列表] ---
    {jobs_text}
    --- [岗位列表结束] ---
    
    请为我执行以下任务：
    1.  仔细阅读我的简历，理解我的技能、经验和求职意向。
    2.  仔细阅读所有岗位，尤其是岗位名称(name)和薪水(salary)。
    3.  从列表中，为我挑选出 **3个** 和我**最匹配**的岗位。
    4.  以友好的、对话式的口吻向我推荐这3个岗位。
    5.  你的回答**必须**包含这3个岗位的【岗位名称】、【薪水】、【公司名】以及【positionURL】(那个投递链接)。
    6.  在推荐每个岗位时，请简短说明【推荐理由】(例如：这个岗位和你的Java经验很匹配)。
    
    请直接开始你的推荐，例如："你好，根据你的简历，我为你挑选了3个最匹配的岗位："
    """
    
    print("阶段 4/4: 正在调用 DeepSeek (任务2：最终匹配)...")
    try:
        response = client_deepseek.chat.completions.create(
            model="deepseek-chat", 
            messages=[{"role": "user", "content": prompt_task_2}],
            temperature=0.5 
        )
        
        final_recommendation = response.choices[0].message.content
        print("--- “一条龙”服务全部完成！---")
        
        return {"final_recommendation": final_recommendation}

    except Exception as e:
        print(f"调用 DeepSeek API (任务2) 失败: {e}")
        return {"error": f"调用 DeepSeek API (任务2) 失败: {e}"}