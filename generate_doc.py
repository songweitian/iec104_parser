"""
生成用户使用说明书（Word格式）
"""

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from version import APP_VERSION, AUTHOR, EMAIL, BUILD_DATE


def create_manual():
    doc = Document()

    # 设置默认字体
    style = doc.styles['Normal']
    font = style.font
    font.name = '微软雅黑'
    font.size = Pt(11)

    # 封面
    doc.add_paragraph()
    doc.add_paragraph()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run('IEC60870-5-104\n报文解析器')
    run.font.size = Pt(28)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x21, 0x96, 0xF3)

    doc.add_paragraph()
    subtitle = doc.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run('用户使用说明书')
    run.font.size = Pt(18)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_paragraph()
    doc.add_paragraph()
    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = info.add_run(f'版本: v{APP_VERSION}\n作者: {AUTHOR}\n邮箱: {EMAIL}\n日期: {BUILD_DATE}')
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    doc.add_page_break()

    # 目录
    doc.add_heading('目录', level=1)
    toc_items = [
        '1. 软件概述',
        '2. 安装与启动',
        '3. 界面介绍',
        '4. 输入报文格式',
        '5. 解析报文',
        '6. 查看解析结果',
        '7. 过滤与搜索',
        '8. 导出功能',
        '9. 书签功能',
        '10. 工具功能',
        '11. 快捷键一览',
        '12. 注册激活',
        '13. 常见问题',
    ]
    for item in toc_items:
        doc.add_paragraph(item, style='List Number')

    doc.add_page_break()

    # 1. 软件概述
    doc.add_heading('1. 软件概述', level=1)
    doc.add_paragraph(
        'IEC60870-5-104 报文解析器是一款基于 IEC 60870-5-104 (2002版) 协议的报文解析软件。'
        '它支持主站/子站双向报文解析，提供直观的图形化界面，帮助工程师快速分析和调试 IEC104 通信报文。'
    )

    doc.add_heading('主要功能：', level=2)
    features = [
        '支持 I帧/S帧/U帧 三种帧类型解析',
        '支持 64 种 TypeID 的完整解析',
        '自动识别普通格式、XN3000、XN8000 三种输入格式',
        '支持 PCAP 文件导入（Wireshark 抓包文件）',
        '多标签页输入，可同时处理多份报文',
        '书签标记、报文定位、正则搜索',
        'CSV/JSON/TXT 三种格式导出',
        '深色/浅色主题切换',
        '帧生成器：生成符合标准的 IEC104 报文',
        '报文对比：并排对比两组报文差异',
        '注册激活：硬件绑定的授权机制',
    ]
    for f in features:
        doc.add_paragraph(f, style='List Bullet')

    # 2. 安装与启动
    doc.add_heading('2. 安装与启动', level=1)

    doc.add_heading('2.1 运行环境', level=2)
    doc.add_paragraph('• Python 3.8+（源码运行时需要）')
    doc.add_paragraph('• PyQt5 >= 5.15.0（源码运行时需要）')
    doc.add_paragraph('• Windows 7/10/11（exe版本）')

    doc.add_heading('2.2 直接运行源码', level=2)
    doc.add_paragraph('步骤1：安装依赖')
    doc.add_paragraph('pip install PyQt5', style='No Spacing')
    doc.add_paragraph('步骤2：运行程序')
    doc.add_paragraph('python main.py', style='No Spacing')

    doc.add_heading('2.3 使用 exe 版本', level=2)
    doc.add_paragraph('1. 解压 IEC104_Parser.zip')
    doc.add_paragraph('2. 双击 IEC104_Parser.exe 运行')
    doc.add_paragraph('3. 无需安装 Python 或任何依赖')

    # 3. 界面介绍
    doc.add_heading('3. 界面介绍', level=1)
    doc.add_paragraph('软件界面分为以下几个区域：')

    doc.add_heading('3.1 菜单栏', level=2)
    doc.add_paragraph('• 文件(F)：导入、导出、新建标签页、退出')
    doc.add_paragraph('• 查看(V)：解析、清空、定位、主题切换、折叠面板、书签导航')
    doc.add_paragraph('• 工具(T)：报文对比、帧生成器、历史管理、协议检查、IOA别名')
    doc.add_paragraph('• 帮助(H)：软件激活、版本信息、IOA地址说明')

    doc.add_heading('3.2 工具栏', level=2)
    doc.add_paragraph('常用操作的快捷按钮：解析、导入、生成报文、导出CSV/JSON、隐藏/显示输入、关于')

    doc.add_heading('3.3 左侧面板', level=2)
    doc.add_paragraph('• 多标签页输入区：粘贴十六进制报文')
    doc.add_paragraph('• 解析模式选择：自动检测/普通/XN3000/XN8000')
    doc.add_paragraph('• 过滤条件：信号类型、类型标识、传送原因、公共地址、IOA范围')

    doc.add_heading('3.4 右侧面板', level=2)
    doc.add_paragraph('• 解析详情（树形视图）：展示各字段详细信息')
    doc.add_paragraph('• 报文列表（表格视图）：所有报文的概览')
    doc.add_paragraph('• 原始十六进制：格式化的原始数据')

    doc.add_heading('3.5 状态栏', level=2)
    doc.add_paragraph('显示统计信息（总报文数、有效/无效、帧类型、五遥统计）和进度条')

    # 4. 输入报文格式
    doc.add_heading('4. 输入报文格式', level=1)

    doc.add_heading('4.1 普通十六进制格式', level=2)
    doc.add_paragraph('每行一条报文，支持空格分隔或不带空格：')
    doc.add_paragraph('68 04 01 00 0E 00', style='No Spacing')
    doc.add_paragraph('680401000E00', style='No Spacing')

    doc.add_heading('4.2 通信日志格式', level=2)
    doc.add_paragraph('支持带时间戳和方向标记的日志：')
    doc.add_paragraph('13:25:00.692 RX:', style='No Spacing')
    doc.add_paragraph('68 15 15 16 0A 00 0B 01 03 00 01 00 0A 40 00', style='No Spacing')

    doc.add_heading('4.3 点分隔格式（XN3000）', level=2)
    doc.add_paragraph('十六进制字节间用点分隔：')
    doc.add_paragraph('68.15.15.16.0a.00', style='No Spacing')

    doc.add_heading('4.4 文件导入', level=2)
    doc.add_paragraph('支持导入的文件格式：')
    doc.add_paragraph('• .txt / .log / .hex / .dat / .bin：文本格式报文')
    doc.add_paragraph('• .pcap：Wireshark 抓包文件')

    doc.add_heading('4.5 拖拽导入', level=2)
    doc.add_paragraph('直接将文件拖放到软件窗口即可导入')

    # 5. 解析报文
    doc.add_heading('5. 解析报文', level=1)

    doc.add_heading('5.1 解析步骤', level=2)
    doc.add_paragraph('1. 在左侧输入框粘贴报文数据（或导入文件）')
    doc.add_paragraph('2. 选择解析模式（推荐：自动检测）')
    doc.add_paragraph('3. 点击「▶ 解析报文」按钮或按 F5')
    doc.add_paragraph('4. 右侧面板显示解析结果')

    doc.add_heading('5.2 解析模式说明', level=2)
    table = doc.add_table(rows=4, cols=3)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = '模式'
    hdr[1].text = '格式特征'
    hdr[2].text = '适用场景'
    row1 = table.rows[1].cells
    row1[0].text = '自动检测'
    row1[1].text = '自动识别格式'
    row1[2].text = '推荐使用'
    row2 = table.rows[2].cells
    row2[0].text = '普通模式'
    row2[1].text = '纯十六进制'
    row2[2].text = '手动输入'
    row3 = table.rows[3].cells
    row3[0].text = 'XN3000'
    row3[1].text = 'RX/TX + 时间戳'
    row3[2].text = 'XN3000设备日志'

    # 6. 查看解析结果
    doc.add_heading('6. 查看解析结果', level=1)

    doc.add_heading('6.1 解析详情（树形视图）', level=2)
    doc.add_paragraph('展示每条报文的详细字段信息：')
    doc.add_paragraph('• APCI：帧类型、发送/接收序列号')
    doc.add_paragraph('• ASDU：类型标识、传送原因、公共地址')
    doc.add_paragraph('• 信息元素：IOA地址、值、品质描述词、时标')
    doc.add_paragraph('点击树节点可在十六进制视图中高亮对应字节')

    doc.add_heading('6.2 报文列表（表格视图）', level=2)
    doc.add_paragraph('所有报文的概览表格，包含12列：')
    doc.add_paragraph('序号、原始时间、方向、信号类型、类型标识、传送原因、公共地址、对象数、值、描述、输入行、时间间隔')
    doc.add_paragraph('行颜色含义：蓝色=数据帧、橙色=控制帧、绿色=S帧、粉色=U帧、红色=错误')

    doc.add_heading('6.3 原始十六进制', level=2)
    doc.add_paragraph('格式化显示原始报文字节，支持字段级高亮')

    # 7. 过滤与搜索
    doc.add_heading('7. 过滤与搜索', level=1)

    doc.add_heading('7.1 过滤条件', level=2)
    doc.add_paragraph('• 信号类型：遥信/遥测/遥控/遥调/遥脉')
    doc.add_paragraph('• 类型标识：按 TypeID 过滤')
    doc.add_paragraph('• 传送原因：按 COT 过滤')
    doc.add_paragraph('• 公共地址：按 ASDU 地址过滤')
    doc.add_paragraph('• IOA 范围：按起始/结束地址过滤')

    doc.add_heading('7.2 搜索功能', level=2)
    doc.add_paragraph('在搜索框输入关键字或正则表达式，实时过滤表格内容')
    doc.add_paragraph('快捷键：Ctrl+F 聚焦搜索框')

    # 8. 导出功能
    doc.add_heading('8. 导出功能', level=1)

    table = doc.add_table(rows=4, cols=3)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = '格式'
    hdr[1].text = '说明'
    hdr[2].text = '快捷键'
    row1 = table.rows[1].cells
    row1[0].text = 'CSV'
    row1[1].text = 'Excel兼容，含UTF-8 BOM'
    row1[2].text = 'Ctrl+Shift+C'
    row2 = table.rows[2].cells
    row2[0].text = 'JSON'
    row2[1].text = '结构化数据'
    row2[2].text = 'Ctrl+Shift+J'
    row3 = table.rows[3].cells
    row3[0].text = 'TXT'
    row3[1].text = '人类可读文本'
    row3[2].text = 'Ctrl+Shift+T'

    doc.add_paragraph()
    doc.add_paragraph('提示：当设置了过滤条件时，只导出过滤后的可见行')

    # 9. 书签功能
    doc.add_heading('9. 书签功能', level=1)
    doc.add_paragraph('• 添加/取消书签：在报文列表中按 Space 键')
    doc.add_paragraph('• 下一个书签：Ctrl+Down')
    doc.add_paragraph('• 上一个书签：Ctrl+Up')
    doc.add_paragraph('书签标记以 📌 显示在序号列')

    # 10. 工具功能
    doc.add_heading('10. 工具功能', level=1)

    doc.add_heading('10.1 帧生成器', level=2)
    doc.add_paragraph('根据参数生成符合 IEC104 标准的报文帧：')
    doc.add_paragraph('• 支持 I/S/U 三种帧类型')
    doc.add_paragraph('• 可配置 TypeID、COT、公共地址、IOA、值')
    doc.add_paragraph('• 一键复制生成的报文')
    doc.add_paragraph('快捷键：Ctrl+G')

    doc.add_heading('10.2 报文对比', level=2)
    doc.add_paragraph('并排对比两组报文，自动检测 IOA 差异')
    doc.add_paragraph('快捷键：Ctrl+Shift+M')

    doc.add_heading('10.3 历史会话管理', level=2)
    doc.add_paragraph('• 保存当前解析会话')
    doc.add_paragraph('• 加载历史会话')
    doc.add_paragraph('• 删除不需要的会话')
    doc.add_paragraph('快捷键：Ctrl+Shift+R')

    doc.add_heading('10.4 IOA 地址别名', level=2)
    doc.add_paragraph('为 IOA 地址设置易记的别名，在解析结果中显示')
    doc.add_paragraph('快捷键：Ctrl+Shift+I')

    # 11. 快捷键一览
    doc.add_heading('11. 快捷键一览', level=1)

    shortcuts = [
        ('F5', '解析报文'),
        ('F2', '定位原始报文'),
        ('Ctrl+N', '新建输入标签页'),
        ('Ctrl+B', '折叠/展开输入面板'),
        ('Ctrl+L', '清空'),
        ('Ctrl+F', '查找'),
        ('Ctrl+T', '统计图表'),
        ('Ctrl+G', '帧生成器'),
        ('Ctrl+I', '导入文件'),
        ('Ctrl+Shift+C', '导出CSV'),
        ('Ctrl+Shift+J', '导出JSON'),
        ('Ctrl+Shift+T', '导出TXT'),
        ('Ctrl+Shift+D', '切换主题'),
        ('Ctrl+Shift+M', '报文对比'),
        ('Ctrl+Shift+R', '历史报文管理'),
        ('Ctrl+1/2/3', '切换结果面板标签页'),
        ('Ctrl+Up/Down', '上/下一个书签'),
        ('Space', '切换书签'),
        ('Enter', '显示解析详情'),
    ]

    table = doc.add_table(rows=len(shortcuts)+1, cols=2)
    table.style = 'Table Grid'
    table.rows[0].cells[0].text = '快捷键'
    table.rows[0].cells[1].text = '功能'
    for i, (key, desc) in enumerate(shortcuts):
        table.rows[i+1].cells[0].text = key
        table.rows[i+1].cells[1].text = desc

    # 12. 注册激活
    doc.add_heading('12. 注册激活', level=1)

    doc.add_heading('12.1 未激活限制', level=2)
    doc.add_paragraph('未激活的软件仅支持单条报文解析功能，以下功能需要激活后使用：')
    doc.add_paragraph('• 文件导入')
    doc.add_paragraph('• 导出 CSV/JSON/TXT')
    doc.add_paragraph('• 帧生成器')
    doc.add_paragraph('• 报文对比')
    doc.add_paragraph('• 历史会话管理')
    doc.add_paragraph('• 多标签页输入')

    doc.add_heading('12.2 激活步骤', level=2)
    doc.add_paragraph('1. 打开软件，点击 帮助 → 软件激活')
    doc.add_paragraph('2. 复制「硬件指纹」')
    doc.add_paragraph('3. 将硬件指纹发送给开发者（邮箱：3028905947@qq.com）')
    doc.add_paragraph('4. 开发者生成激活码并返回')
    doc.add_paragraph('5. 在激活页面输入激活码，点击「激活」')

    doc.add_heading('12.3 取消激活', level=2)
    doc.add_paragraph('删除项目目录下的 license.dat 文件即可取消激活')

    # 13. 常见问题
    doc.add_heading('13. 常见问题', level=1)

    doc.add_heading('Q: 软件打不开怎么办？', level=2)
    doc.add_paragraph('A: exe版本需要 Windows 7 以上系统。如果提示缺少 DLL，请安装 VC++ 运行时。')

    doc.add_heading('Q: 解析结果为空怎么办？', level=2)
    doc.add_paragraph('A: 检查输入格式是否正确，确保以 0x68 开头。尝试切换解析模式。')

    doc.add_heading('Q: 如何查看原始报文？', level=2)
    doc.add_paragraph('A: 在报文列表中右键点击，选择「定位原始报文」或按 F2。')

    doc.add_heading('Q: 激活码失效怎么办？', level=2)
    doc.add_paragraph('A: 激活码与硬件绑定，更换电脑需要重新激活。请联系开发者。')

    doc.add_heading('Q: 如何导入 Wireshark 抓包？', level=2)
    doc.add_paragraph('A: 将 .pcap 文件直接拖放到软件窗口，或使用文件 → 导入功能。')

    # 页脚
    doc.add_paragraph()
    doc.add_paragraph()
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run('— IEC60870-5-104 报文解析器 用户使用说明书 —')
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    footer2 = doc.add_paragraph()
    footer2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer2.add_run('作者：宋伟田 | 邮箱：3028905947@qq.com')
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    doc.save('IEC104_Parser_用户使用说明书.docx')
    print('文档已生成: IEC104_Parser_用户使用说明书.docx')


if __name__ == '__main__':
    create_manual()
