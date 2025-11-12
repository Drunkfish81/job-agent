import sqlite3
import chromadb
from sentence_transformers import SentenceTransformer

# --- 配置 ---
DATABASE_FILE = 'jobs.db'      # 你的 SQLite 数据库
COLLECTION_NAME = "job_postings" # 向量数据库里的“表”名
CHROMA_PATH = "chroma_db"      # 向量数据库的本地存储路径
MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2' # 一个小型的、多语言AI模型

def get_jobs_from_sqlite():
    """从 SQLite 数据库读取所有工作"""
    print(f"正在从 {DATABASE_FILE} 读取所有岗位...")
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, name, salary, companyName, positionURL FROM jobs")
    jobs = cursor.fetchall()
    conn.close()
    
    print(f"成功读取 {len(jobs)} 条岗位。")
    return jobs

def main():
    # 1. 加载 AI 模型
    # (这一步你已经成功了, 它会使用缓存, 速度会很快)
    print(f"正在加载 AI 模型: {MODEL_NAME} ...")
    model = SentenceTransformer(MODEL_NAME)
    print("AI 模型加载完毕。")

    # 2. 连接到 ChromaDB (向量数据库)
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    
    # 3. 清理并创建 Collection
    try:
        client.delete_collection(name=COLLECTION_NAME)
        print(f"已删除旧的 Collection: {COLLECTION_NAME}")
    except Exception as e:
        print(f"没有旧的 Collection: {COLLECTION_NAME}, 准备新建。")

    collection = client.create_collection(name=COLLECTION_NAME)
    print(f"向量数据库 Collection '{COLLECTION_NAME}' 已准备就绪。")

    # 4. 从 SQLite 读取数据
    jobs_to_index = get_jobs_from_sqlite()
    
    if not jobs_to_index:
        print("数据库中没有岗位，请先运行 data_collector.py")
        return

    # 5. 准备数据给 ChromaDB
    documents = []  # 文档：我们要让AI"理解"的文本
    metadatas = []  # 元数据：我们希望AI在"找到"时一并返回的信息
    ids = []        # 唯一ID
    
    print("正在准备数据，请稍候...")
    for job in jobs_to_index:
        doc_text = f"岗位: {job['name']}, 公司: {job['companyName']}, 薪水: {job['salary']}"
        documents.append(doc_text)
        
        metadatas.append({
            "name": job['name'],
            "salary": job['salary'],
            "companyName": job['companyName'],
            "positionURL": job['positionURL']
        })
        
        ids.append(str(job['id']))

    # ----------------------------------------------------------------
    # 
    # !!! --- 这是修复的核心 --- !!!
    #
    # 6. (新) 手动进行向量化
    # 我们使用我们自己加载的 model，而不是让 ChromaDB 去下载它那个
    print(f"\n正在使用 {MODEL_NAME} 对 {len(documents)} 条文档进行向量化...")
    embeddings_list = model.encode(documents, show_progress_bar=True)
    print("向量化完毕。")

    # 7. (修改) 将所有数据 (包括手动生成的向量) 存入数据库
    print("正在将数据存入向量数据库...")
    collection.add(
        embeddings=embeddings_list, # <--- 明确传入我们生成的向量
        documents=documents,
        metadatas=metadatas,
        ids=ids
    )



if __name__ == "__main__":
    main()