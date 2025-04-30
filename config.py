import os

# 基础目录配置
BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'managed_files')

# 数据库配置
DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'filemanager.db')

# 允许预览的文件类型
ALLOWED_PREVIEW_TYPES = {
    'image': ['.jpg', '.jpeg', '.png', '.gif'],
    'video': ['.mp4', '.webm'],
    'pdf': ['.pdf'],
    'text': ['.txt', '.md'],
    'office': ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
}

# 日志清理配置
LOG_CLEANUP_MONTHS = 3  # 保留最近3个月的访问日志，超过时间的记录将被自动清理

# 应用配置
SECRET_KEY = 'your-secret-key-here'
DEBUG = True