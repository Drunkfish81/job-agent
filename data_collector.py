import requests
import json
import sqlite3 # <--- 新增：用于操作数据库
import time      # <--- 新增：用于礼貌地暂停

# 1. 填入你"偷"来的所有东西
# ----------------------------------------------------

# (填入"干净"的URL, 也就是 ? 之前的部分)
url = "https://fe-api.zhaopin.com/c/i/search/positions" 

# (你之前找到的 Headers)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 ', # 替换成你的User-Agent
    'Cookie': 'x-zp-client-id=dc74f3ce-951b-4076-8da5-063699dbf6cf; sajssdk_2015_cross_new_user=1; sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219a78024897e0-0bbfbc937d5dc3-26011b51-1821369-19a78024898247%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E7%9B%B4%E6%8E%A5%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC_%E7%9B%B4%E6%8E%A5%E6%89%93%E5%BC%80%22%2C%22%24latest_referrer%22%3A%22%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTlhNzgwMjQ4OTdlMC0wYmJmYmM5MzdkNWRjMy0yNjAxMWI1MS0xODIxMzY5LTE5YTc4MDI0ODk4MjQ3In0%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219a78024897e0-0bbfbc937d5dc3-26011b51-1821369-19a78024898247%22%7D; sensorsdata2015jssdkchannel=%7B%22prop%2E%7B%22_sa_channel_landing_url%22%3A%22%22%7D%7D; Hm_lvt_7fa4effa4233f03d11c7e2c710749600=1762950011; HMACCOUNT=8969172F52244AB1; Hm_lpvt_7fa4effa4233f03d11c7e2c710749600=1762950020; LastCity=undefined; LastCity%5Fid=undefined'         # 替换成你的Cookie
}

# (你刚找到的Payload, 也就是那个JSON)
payload_data = {
    "S_SOU_FULL_INDEX": "java",  # <--- 搜索关键词
    "S_SOU_WORK_CITY": "489",      # <--- 城市代码 (489=北京)
    "pageIndex": 1,                # <--- 想爬第几页
    "pageSize": 20,
    "order": 4,
    "anonymous": 1,
    "eventScenario": "pcSearchedSouSearch",
    "platform": 13,
    "version": "0.0.0"
}
# ----------------------------------------------------


# ----------------------------------------------------
# 步骤 1.A: 数据库操作函数 (新代码)
# ----------------------------------------------------

DATABASE_FILE = 'jobs.db' # 数据库文件的名字

def setup_database():
    """
    在脚本运行时，确保数据库文件和 'jobs' 表格存在。
    """
    # 这会在你的 .py 旁边创建一个叫 'jobs.db' 的文件
    conn = sqlite3.connect(DATABASE_FILE) 
    cursor = conn.cursor()
    
    # 我们创建一张表叫 'jobs'
    # positionURL 设置为 UNIQUE (独一无二) 是个【专业技巧】
    # 这能防止你重复添加同一个岗位
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        salary TEXT,
        companyName TEXT,
        positionURL TEXT UNIQUE  
    )
    ''')
    conn.commit() # 提交更改
    conn.close()
    print(f"Database '{DATABASE_FILE}' and table 'jobs' are ready.")

def save_job_to_db(job_data):
    """
    把一个 'job' 字典存入数据库
    返回 True 如果成功插入, 返回 False 如果忽略了 (因为重复)
    """
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        # 使用 INSERT OR IGNORE 
        # 如果 positionURL 已经存在 (因为 UNIQUE), 这一行就会被自动忽略
        cursor.execute('''
        INSERT OR IGNORE INTO jobs (name, salary, companyName, positionURL)
        VALUES (?, ?, ?, ?)
        ''', (
            job_data.get('name'),
            job_data.get('salary60'),
            job_data.get('companyName'),
            job_data.get('positionURL')
        ))
        conn.commit()
        # .rowcount 会告诉我们上一条命令影响了多少行 (1=插入, 0=忽略)
        return cursor.rowcount > 0 
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False
    finally:
        conn.close()

# ----------------------------------------------------
# 步骤 1.B: 主程序 (修改后的)
# ----------------------------------------------------

def main():
    # 在所有请求开始前，先准备好数据库
    setup_database()
    
    # 你可以修改这里，让它爬取不止一页
    # 比如: for page_num in range(1, 6): # 爬取第1到第5页
    # -------------------------------------------------
    page_to_crawl = payload_data['pageIndex'] 
    
    # 更新 payload 里的页码 (如果我们用了循环, 这会很有用)
    payload_data['pageIndex'] = page_to_crawl
    # -------------------------------------------------

    keyword = payload_data['S_SOU_FULL_INDEX']
    
    print(f"\n--- 正在请求第 {page_to_crawl} 页的 '{keyword}' 数据... ---")

    session = requests.Session()
    try:
        response = session.post(url, headers=headers, json=payload_data, timeout=10) 

        if response.status_code == 200:
            print("请求成功！服务器返回了数据！")
            
            data = response.json()
            job_list = data.get('data', {}).get('list', []) 
            
            if not job_list:
                print("警告：请求成功，但没找到岗位数据。")
            else:
                print(f"成功解析到 {len(job_list)} 个岗位！正在存入数据库...")
                
                new_jobs_count = 0
                for job in job_list:
                    # 我们调用存入数据库的函数
                    if save_job_to_db(job):
                        new_jobs_count += 1
                
                print(f"处理完毕: {new_jobs_count} 个新岗位被存入, {len(job_list) - new_jobs_count} 个岗位已存在被忽略。")
                
        else:
            print(f"请求失败，HTTP状态码: {response.status_code}")
            print(f"服务器说: {response.text}")

    except requests.RequestException as e:
        print(f"网络请求出错了: {e}")
    except json.JSONDecodeError:
        print("服务器返回的不是JSON，是不是被反爬了？")
        print("服务器返回内容:", response.text)

    # (推荐) 友好一点，每次请求之间歇一会儿
    # print("休息1秒钟...")
    # time.sleep(1) 

# ----------------------------------------------------
# 脚本的入口
# ----------------------------------------------------
if __name__ == "__main__":
    main()