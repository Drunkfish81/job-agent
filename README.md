# AI 智能招聘 Agent

这是一个“一条龙”的智能招聘项目。它跳过了传统的“海投”，而是让 AI 读取你的简历，然后**实时爬取**最新的岗位，最后由 AI 扮演 HR 为你**精准匹配**最好的 3 个机会。

---

## 🚀 核心工作流 (简历智能匹配)

这是本项目的“王牌”功能，整个逻辑是全自动的：

1.  **[前端] 上传简历：**
    用户在 `index.html` 网页上选择自己的 PDF 简历并点击“上传”。

2.  **[后端] 读取简历：**
    `FastAPI` 服务器接收文件，并使用 `pypdf` 库提取简历中的所有文本。

3.  **[AI 任务 1] 分析简历，生成关键词：**
    后端将“简历纯文本”发送给 **DeepSeek API**，让 AI 扮演 HR，分析简历并返回核心**搜索关键词** (如 "Java后端" 或 "Python数据分析")。

4.  **[后端] 启动实时爬虫：**
    后端拿到关键词后，**立即**启动爬虫，伪造 `Cookie` 请求智联招聘的**后端 API**。
    
5.  **[后端] 抓取数据：**
    爬虫**循环 10 次**，抓取该关键词下最新、最热的 10 页岗位（约 200 个），并整理成 JSON。

6.  **[AI 任务 2] 角色扮演，智能匹配：**
    后端将“简历原文” + “刚爬到的 200 个岗位”打包，**再次**发送给 **DeepSeek API**，让 AI 扮演顶尖 HR，从 200 个岗位中为你精选出最匹配的 3 个。

7.  **[前端] 返回最终推荐：**
    前端网页收到 AI 返回的“专属推荐信”，并将其展示给用户。这份推荐信包含了**薪资、公司、投递链接**以及**推荐理由**。

*(注：目前只做到了推荐，“一键投递”的功能还没做。)*

---

## 🗂️ 文件结构说明

| 文件名 | 用途 |
| :--- | :--- |
| **`ai_agent_server.py`** | **[核心]** 你的“总指挥部”。运行 FastAPI 服务器，掌管所有 API 接口（简历分析、爬虫、AI调用、搜索）。|
| **`index.html`** | **[核心]** 你的“脸面”。用户唯一需要交互的前端网页。|
| `requirements.txt` | **[配置]** 安装清单。记录了项目所有的 Python 依赖库。|
| `README.md` | **[配置]** 说明书（就是本文件）。|

以下是可以不用运行的，这个是手动语义搜索
| `data_collector.py` | [工具] 离线矿工。用于爬取“静态”数据，并存入 `jobs.db`。|
| `vector_indexer.py` | [工具] AI 索引器。用于读取 `jobs.db` 并将其“翻译”存入 `chroma_db`，为“语义搜索”功能服务。|
| `jobs.db` | [数据] 爬虫的“静态仓库”。由 `data_collector.py` 生成。|
| `chroma_db/` | [数据] AI 的“大脑”。由 `vector_indexer.py` 生成的向量索引库。|

---

## ⚙️ 如何在本地运行

1.  **克隆/下载项目：**
    ```bash
    git clone [https://github.com/Drunkfish81/job-agent.git](https://github.com/Drunkfish81/job-agent.git)
    cd job-agent
    ```

2.  **安装所有依赖:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **填入你的 Key 和 Cookie：**
    打开 `.env` 文件，填入你自己的“秘密”，格式如下：
    ```
    DEEPSEEK_API_KEY="sk-xxxxxx... (你的DeepSeek Key)"
    ZHAOPIN_COOKIE="x-zp-client-id=... (你自己去智联偷的Cookie)"
    ```

4.  **（可选）生成静态搜索库:**
    如果你想让“手动语义搜索”功能也生效，请先运行这两个脚本：
    ```bash
    python data_collector.py
    python vector_indexer.py
    ```
    其实也可以完全不用运行

5.  **启动“总指挥部”:**
    ```bash
    uvicorn ai_agent_server:app --reload
    ```

6.  **打开网页：**
    在浏览器中打开 `index.html` 文件（或者访问 `http://127.0.0.1:8000/`，如果你配置了静态文件服务的话）。
