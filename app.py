import os
import time
import sqlite3
import json
from datetime import datetime
from flask import Flask, render_template, request, send_from_directory, redirect, url_for, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config.from_pyfile('config.py')

# 确保目录存在
def ensure_dir_exists(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

# 初始化数据库
def init_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    # 访问日志表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS access_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL,
        access_time TIMESTAMP NOT NULL,
        ip_address TEXT NOT NULL,
        user_agent TEXT
    )
    ''')
    # 文件访问次数统计表（长期保存）
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS file_access_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_path TEXT NOT NULL UNIQUE,
        access_count INTEGER NOT NULL DEFAULT 0,
        first_access TIMESTAMP,
        last_access TIMESTAMP,
        direct_link_enabled INTEGER NOT NULL DEFAULT 1
    )
    ''')
    conn.commit()
    conn.close()
    
# 清理旧的访问日志
def clean_old_logs():
    # 获取清理阈值（默认3个月）
    months = app.config.get('LOG_CLEANUP_MONTHS', 3)
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    cursor.execute(
        'DELETE FROM access_logs WHERE access_time < datetime("now", ?)',
        (f'-{months} months',)
    )
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"已清理 {deleted_count} 条旧访问记录")
    return deleted_count

# 记录访问日志
def log_access(file_path, ip_address, user_agent):
    now = datetime.now()
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    
    # 记录详细访问日志
    cursor.execute(
        'INSERT INTO access_logs (file_path, access_time, ip_address, user_agent) VALUES (?, ?, ?, ?)',
        (file_path, now, ip_address, user_agent)
    )
    
    # 更新文件访问统计
    cursor.execute(
        '''
        INSERT INTO file_access_stats (file_path, access_count, first_access, last_access) 
        VALUES (?, 1, ?, ?) 
        ON CONFLICT(file_path) DO UPDATE SET 
        access_count = access_count + 1, 
        last_access = ?
        ''',
        (file_path, now, now, now)
    )
    
    conn.commit()
    conn.close()

# 获取文件类型
def get_file_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.gif']:
        return 'image'
    elif ext in ['.mp4', '.webm']:
        return 'video'
    elif ext in ['.pdf']:
        return 'pdf'
    elif ext in ['.txt', '.md']:
        return 'text'
    elif ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
        return 'office'
    else:
        return 'other'

# 获取目录内容
def get_directory_contents(directory):
    items = []
    for item in os.listdir(directory):
        path = os.path.join(directory, item)
        is_dir = os.path.isdir(path)
        if is_dir:
            item_type = 'directory'
        else:
            item_type = get_file_type(item)
        
        # 获取文件大小和修改时间
        stats = os.stat(path)
        size = stats.st_size if not is_dir else 0
        modified = datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        
        # 检查直链状态
        direct_link_enabled = 1
        if not is_dir:
            rel_path = os.path.relpath(path, app.config['BASE_DIR'])
            conn = sqlite3.connect(app.config['DATABASE'])
            cursor = conn.cursor()
            cursor.execute('SELECT direct_link_enabled FROM file_access_stats WHERE file_path = ?', (rel_path,))
            result = cursor.fetchone()
            if result:
                direct_link_enabled = result[0]
            conn.close()
        
        items.append({
            'name': item,
            'path': path,
            'type': item_type,
            'is_dir': is_dir,
            'size': size,
            'modified': modified,
            'direct_link': url_for('serve_file', path=os.path.relpath(path, app.config['BASE_DIR']), _external=True) if not is_dir and direct_link_enabled else None,
            'direct_link_enabled': direct_link_enabled if not is_dir else None,
            'rel_path': os.path.relpath(path, app.config['BASE_DIR']) if not is_dir else None
        })
    
    # 按照文件夹在前，文件在后的顺序排序
    return sorted(items, key=lambda x: (0 if x['is_dir'] else 1, x['name']))

# 主页路由
@app.route('/')
def index():
    return redirect(url_for('browse', path=''))

# 浏览文件路由
@app.route('/browse/<path:path>')
@app.route('/browse/', defaults={'path': ''})
def browse(path):
    # 确定当前目录
    if path:
        current_dir = os.path.join(app.config['BASE_DIR'], path)
    else:
        current_dir = app.config['BASE_DIR']
    
    # 检查路径是否在允许的目录内
    if not os.path.commonpath([current_dir, app.config['BASE_DIR']]) == app.config['BASE_DIR']:
        return "访问被拒绝：目录不在允许范围内", 403
    
    # 检查目录是否存在
    if not os.path.exists(current_dir) or not os.path.isdir(current_dir):
        return "目录不存在", 404
    
    # 获取目录内容
    items = get_directory_contents(current_dir)
    
    # 构建导航路径
    nav_path = []
    if path:
        parts = path.split('/')
        for i, part in enumerate(parts):
            nav_path.append({
                'name': part,
                'path': '/'.join(parts[:i+1])
            })
    
    return render_template('index.html', 
                           items=items, 
                           current_path=path, 
                           nav_path=nav_path)

# 文件预览路由
@app.route('/preview/<path:path>')
def preview(path):
    file_path = os.path.join(app.config['BASE_DIR'], path)
    
    # 检查路径是否在允许的目录内
    if not os.path.commonpath([file_path, app.config['BASE_DIR']]) == app.config['BASE_DIR']:
        return "访问被拒绝：文件不在允许范围内", 403
    
    # 检查文件是否存在
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        return "文件不存在", 404
    
    file_type = get_file_type(file_path)
    file_name = os.path.basename(file_path)
    
    # 检查直链状态
    direct_link_enabled = 1
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    cursor.execute('SELECT direct_link_enabled FROM file_access_stats WHERE file_path = ?', (path,))
    result = cursor.fetchone()
    if result:
        direct_link_enabled = result[0]
    conn.close()

    if direct_link_enabled:
        direct_link = f'../file/{path}'
    else:
        direct_link = None

    preview_link = f'../preview-file/{path}'
    
    return render_template('preview.html', 
                           file_path=path, 
                           file_name=file_name, 
                           file_type=file_type,
                           direct_link=direct_link,
                           preview_link=preview_link)

# 文件服务路由
@app.route('/file/<path:path>')
def serve_file(path):
    file_path = os.path.join(app.config['BASE_DIR'], path)
    
    # 检查路径是否在允许的目录内
    if not os.path.commonpath([file_path, app.config['BASE_DIR']]) == app.config['BASE_DIR']:
        return "访问被拒绝：文件不在允许范围内", 403
    
    # 检查文件是否存在
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        return "文件不存在", 404
    
    # 检查直链是否启用
    conn = sqlite3.connect(app.config['DATABASE'])
    cursor = conn.cursor()
    cursor.execute('SELECT direct_link_enabled FROM file_access_stats WHERE file_path = ?', (path,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0] == 0:
        return "直链访问已禁用", 403
    
    # 记录访问日志
    log_access(
        file_path=path,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string
    )
    
    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    return send_from_directory(directory, filename)

# 预览专用文件服务路由（不受直链状态影响）
@app.route('/preview-file/<path:path>')
def serve_preview(path):
    file_path = os.path.join(app.config['BASE_DIR'], path)
    
    # 检查路径是否在允许的目录内
    if not os.path.commonpath([file_path, app.config['BASE_DIR']]) == app.config['BASE_DIR']:
        return "访问被拒绝：文件不在允许范围内", 403
    
    # 检查文件是否存在
    if not os.path.exists(file_path) or os.path.isdir(file_path):
        return "文件不存在", 404
    
    # 记录访问日志
    # log_access(
    #     file_path=path,
    #     ip_address=request.remote_addr,
    #     user_agent=request.user_agent.string
    # )

    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    return send_from_directory(directory, filename)

# 统计数据API
@app.route('/api/stats')
def get_stats():
    # 获取筛选参数
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    file_type = request.args.get('file_type')
    ip_address = request.args.get('ip_address')
    
    # 构建基础查询条件
    conditions = []
    params = []
    
    if start_date:
        conditions.append('date(access_time) >= ?')
        params.append(start_date)
    
    if end_date:
        conditions.append('date(access_time) <= ?')
        params.append(end_date)
    
    if ip_address:
        conditions.append('ip_address LIKE ?')
        params.append(f'%{ip_address}%')
    
    # 文件类型筛选需要特殊处理
    file_type_condition = ''
    if file_type:
        if file_type == 'image':
            file_type_condition = "AND (file_path LIKE '%.jpg' OR file_path LIKE '%.jpeg' OR file_path LIKE '%.png' OR file_path LIKE '%.gif')"
        elif file_type == 'video':
            file_type_condition = "AND (file_path LIKE '%.mp4' OR file_path LIKE '%.webm')"
        elif file_type == 'pdf':
            file_type_condition = "AND file_path LIKE '%.pdf'"
        elif file_type == 'text':
            file_type_condition = "AND (file_path LIKE '%.txt' OR file_path LIKE '%.md')"
        elif file_type == 'office':
            file_type_condition = "AND (file_path LIKE '%.doc' OR file_path LIKE '%.docx' OR file_path LIKE '%.xls' OR file_path LIKE '%.xlsx' OR file_path LIKE '%.ppt' OR file_path LIKE '%.pptx')"
    
    # 构建WHERE子句
    where_clause = ''
    if conditions or file_type_condition:
        where_clause = 'WHERE ' + ' AND '.join(conditions) + ' ' + file_type_condition
    
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 获取总访问次数（从长期统计表获取）
    cursor.execute('SELECT SUM(access_count) as count FROM file_access_stats')
    total_count_row = cursor.fetchone()
    total_count = total_count_row['count'] if total_count_row['count'] is not None else 0
    
    # 获取访问趋势（可按日、周、月分组）
    group_by = request.args.get('group_by', 'day')
    date_format = 'date(access_time)'
    if group_by == 'week':
        date_format = "strftime('%Y-%W', access_time)"
    elif group_by == 'month':
        date_format = "strftime('%Y-%m', access_time)"
    
    trend_query = f'''
    SELECT {date_format} as date_group, COUNT(*) as count 
    FROM access_logs 
    {where_clause}
    GROUP BY date_group 
    ORDER BY date_group DESC LIMIT 12
    '''
    cursor.execute(trend_query, params)
    time_trend = [dict(row) for row in cursor.fetchall()]
    
    # 获取访问最多的文件（从长期统计表获取）
    cursor.execute('''
    SELECT file_path, access_count as count, first_access, last_access
    FROM file_access_stats 
    ORDER BY access_count DESC LIMIT 10
    ''')
    top_files = [dict(row) for row in cursor.fetchall()]
    
    # 获取IP分布
    ip_query = f'''
    SELECT ip_address, COUNT(*) as count 
    FROM access_logs 
    {where_clause}
    GROUP BY ip_address 
    ORDER BY count DESC LIMIT 10
    '''
    cursor.execute(ip_query, params)
    ip_distribution = [dict(row) for row in cursor.fetchall()]
    
    # 获取文件类型分布
    file_type_query = f'''
    SELECT 
        CASE 
            WHEN file_path LIKE '%.jpg' OR file_path LIKE '%.jpeg' OR file_path LIKE '%.png' OR file_path LIKE '%.gif' THEN 'image'
            WHEN file_path LIKE '%.mp4' OR file_path LIKE '%.webm' THEN 'video'
            WHEN file_path LIKE '%.pdf' THEN 'pdf'
            WHEN file_path LIKE '%.txt' OR file_path LIKE '%.md' THEN 'text'
            WHEN file_path LIKE '%.doc' OR file_path LIKE '%.docx' OR file_path LIKE '%.xls' OR file_path LIKE '%.xlsx' OR file_path LIKE '%.ppt' OR file_path LIKE '%.pptx' THEN 'office'
            ELSE 'other'
        END as file_type,
        COUNT(*) as count
    FROM access_logs
    {where_clause}
    GROUP BY file_type
    ORDER BY count DESC
    '''
    cursor.execute(file_type_query, params)
    file_type_distribution = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return jsonify({
        'total_count': total_count,
        'time_trend': time_trend,
        'top_files': top_files,
        'ip_distribution': ip_distribution,
        'file_type_distribution': file_type_distribution
    })

# 统计页面路由
@app.route('/stats')
def stats():
    return render_template('stats.html')

# 定时清理任务API
@app.route('/api/clean-logs', methods=['POST'])
def clean_logs_api():
    if request.method == 'POST':
        deleted_count = clean_old_logs()
        return jsonify({
            'success': True,
            'deleted_count': deleted_count,
            'message': f'已清理 {deleted_count} 条旧访问记录'
        })

# 清空统计数据API
@app.route('/api/clear-stats', methods=['POST'])
def clear_stats_api():
    if request.method == 'POST':
        try:
            conn = sqlite3.connect(app.config['DATABASE'])
            cursor = conn.cursor()
            # 清空访问日志表
            cursor.execute('DELETE FROM access_logs')
            # 清空文件访问统计表
            cursor.execute('DELETE FROM file_access_stats')
            conn.commit()
            conn.close()
            return jsonify({
                'success': True,
                'message': '已清空所有统计数据'
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'清空统计数据失败: {str(e)}'
            }), 500

# 文件操作API

# 切换直链状态
@app.route('/api/toggle-direct-link', methods=['POST'])
def toggle_direct_link():
    file_path = request.json.get('file_path')
    enabled = request.json.get('enabled', 0)
    
    if not file_path:
        return jsonify({'success': False, 'message': '缺少文件路径参数'}), 400
    
    full_path = os.path.join(app.config['BASE_DIR'], file_path)
    
    # 检查文件是否存在
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    
    try:
        conn = sqlite3.connect(app.config['DATABASE'])
        cursor = conn.cursor()
        
        # 检查文件是否已有记录
        cursor.execute('SELECT id FROM file_access_stats WHERE file_path = ?', (file_path,))
        result = cursor.fetchone()
        
        if result:
            # 更新直链状态
            cursor.execute('UPDATE file_access_stats SET direct_link_enabled = ? WHERE file_path = ?', 
                          (1 if enabled else 0, file_path))
        else:
            # 创建新记录
            now = datetime.now()
            cursor.execute(
                'INSERT INTO file_access_stats (file_path, access_count, first_access, last_access, direct_link_enabled) VALUES (?, ?, ?, ?, ?)',
                (file_path, 0, now, now, 1 if enabled else 0)
            )
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'已{"启用" if enabled else "禁用"}直链访问',
            'enabled': enabled
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'操作失败: {str(e)}'}), 500

# 删除文件
@app.route('/api/delete-file', methods=['POST'])
def delete_file():
    file_path = request.json.get('file_path')
    
    if not file_path:
        return jsonify({'success': False, 'message': '缺少文件路径参数'}), 400
    
    full_path = os.path.join(app.config['BASE_DIR'], file_path)
    
    # 检查文件是否存在
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    
    try:
        # 删除数据库记录
        conn = sqlite3.connect(app.config['DATABASE'])
        cursor = conn.cursor()
        
        # 删除访问日志
        cursor.execute('DELETE FROM access_logs WHERE file_path = ?', (file_path,))
        
        # 删除统计记录
        cursor.execute('DELETE FROM file_access_stats WHERE file_path = ?', (file_path,))
        
        conn.commit()
        conn.close()
        
        # 删除文件
        os.remove(full_path)
        
        return jsonify({
            'success': True,
            'message': '文件已成功删除'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500

# 重命名文件
@app.route('/api/rename-file', methods=['POST'])
def rename_file():
    old_path = request.json.get('old_path')
    new_name = request.json.get('new_name')
    
    if not old_path or not new_name:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400
    
    full_old_path = os.path.join(app.config['BASE_DIR'], old_path)
    
    # 检查文件是否存在
    if not os.path.exists(full_old_path) or os.path.isdir(full_old_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    
    # 构建新路径
    dir_name = os.path.dirname(old_path)
    new_path = os.path.join(dir_name, new_name) if dir_name else new_name
    full_new_path = os.path.join(app.config['BASE_DIR'], new_path)
    
    # 检查新文件名是否已存在
    if os.path.exists(full_new_path):
        return jsonify({'success': False, 'message': '目标文件名已存在'}), 400
    
    try:
        # 重命名文件
        os.rename(full_old_path, full_new_path)
        
        # 更新数据库记录
        conn = sqlite3.connect(app.config['DATABASE'])
        cursor = conn.cursor()
        
        # 更新访问日志
        cursor.execute('UPDATE access_logs SET file_path = ? WHERE file_path = ?', (new_path, old_path))
        
        # 更新统计记录
        cursor.execute('UPDATE file_access_stats SET file_path = ? WHERE file_path = ?', (new_path, old_path))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': '文件已成功重命名',
            'new_path': new_path
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'重命名失败: {str(e)}'}), 500

# 初始化应用
if __name__ == '__main__':
    # 确保数据目录存在
    ensure_dir_exists(os.path.dirname(app.config['DATABASE']))
    # 初始化数据库
    init_db()
    # 执行一次日志清理
    clean_old_logs()
    # 启动应用
    app.run(debug=True, host='0.0.0.0', port=5000)