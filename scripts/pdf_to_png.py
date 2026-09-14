"""
PDF 转 PNG 脚本（用于 Logo 资产转换）

依赖：
    pip install PyMuPDF

用法：
    python scripts/pdf_to_png.py <input.pdf> <output.png> [dpi]

示例：
    python scripts/pdf_to_png.py "欣正咨询.pdf" frontend/public/logo.png 300
"""
import sys
import os


def pdf_to_png(pdf_path: str, png_path: str, dpi: int = 300):
    try:
        import fitz
    except ImportError:
        print("缺少 PyMuPDF 库，请执行：pip install PyMuPDF")
        sys.exit(1)

    if not os.path.exists(pdf_path):
        print(f"PDF 文件不存在：{pdf_path}")
        sys.exit(1)

    output_dir = os.path.dirname(png_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    print(f"打开 PDF：{pdf_path}")
    doc = fitz.open(pdf_path)
    print(f"  共 {len(doc)} 页，取第 1 页")

    page = doc[0]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=True)
    pix.save(png_path)
    doc.close()

    file_size_kb = os.path.getsize(png_path) / 1024
    print(f"转换成功：{png_path}")
    print(f"  尺寸：{pix.width} x {pix.height} 像素")
    print(f"  文件大小：{file_size_kb:.1f} KB")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法：python scripts/pdf_to_png.py <input.pdf> <output.png> [dpi]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    png_path = sys.argv[2]
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 300

    pdf_to_png(pdf_path, png_path, dpi)