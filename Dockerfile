# DefectCollab 容器镜像
FROM python:3.11-slim

WORKDIR /app

# 依赖先行安装，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（.dockerignore 已排除 .git / 数据库 / 密钥）
COPY . .

# 数据与备份持久化目录（运行时挂载 volume）
RUN mkdir -p /app/data /app/backups
ENV DEFECT_DB=/app/data/defects.db
ENV PORT=8080

EXPOSE 8080

# 首次启动自动建库、种子账号 admin/admin123 + DEMO 工作区
CMD ["python", "server.py"]
