# 我的AI招聘Agent

这是一个能自动分析简历、爬取岗位、并智能匹配的AI Agent。

## 如何运行

1.  **安装依赖:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **创建你自己的 `.env` 文件:**
    在项目根目录创建 `.env` 文件，并填入你自己的Key：
    ```
    DEEPSEEK_API_KEY="你的Key"
    ZHAOPIN_COOKIE="你自己去偷的Cookie"
    ```

3.  **运行索引 (如果需要):**
    ```bash
    python vector_indexer.py
    ```

4.  **启动服务器:**
    ```bash
    uvicorn ai_agent_server:app --reload
    ```