import subprocess
import gzip
import codecs
import glob
import argparse
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time

# 配置日志记录
def setup_logging(log_file="conversion.log", console_level=logging.WARNING, file_level=logging.INFO):
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)  # 捕获所有级别的日志

    # 创建文件处理器
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(file_level)
    fh_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    fh.setFormatter(fh_formatter)
    logger.addHandler(fh)

    # 创建控制台处理器
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    ch.setFormatter(ch_formatter)
    logger.addHandler(ch)

def get_mjai_log(mjai_reviewer_path, tenhou_id, retries=3, delay=2):
    """
    使用 mjai-reviewer.exe 将 Tenhou ID 转换为 mjai 格式的 JSON，并添加重试机制。
    """
    cmd = [
        str(mjai_reviewer_path),
        "-u", f"https://tenhou.net/0/?log={tenhou_id}",
        "--no-review",
        "--mjai-out", "-"
    ]
    for attempt in range(1, retries + 1):
        try:
            logging.debug(f"Running command: {' '.join(cmd)} (Attempt {attempt})")
            # 仅捕获 stdout，不合并 stderr
            output = subprocess.check_output(cmd, stderr=subprocess.PIPE)
            return output.decode('utf-8').rstrip()
        except subprocess.CalledProcessError as e:
            logging.error(f"get_mjai_log failed for ID {tenhou_id} on attempt {attempt}: {e.stderr.decode('utf-8').strip()}")
        except Exception as e:
            logging.error(f"Unexpected error for ID {tenhou_id} on attempt {attempt}: {e}")
        if attempt < retries:
            logging.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
    logging.error(f"All {retries} attempts failed for ID {tenhou_id}")
    return None

def get_id_list(gz_path):
    """
    从 .gz 文件中提取 Tenhou ID 列表。
    """
    ret = []
    try:
        with gzip.open(gz_path, 'rb') as f:
            contents = codecs.getreader("utf-8")(f)
            for line in contents:
                segs = line.split('|')
                if len(segs) < 4:
                    continue
                rule = segs[2].strip()
                if rule.startswith("四鳳南喰赤"):
                    try:
                        tenhou_id = segs[3].split('"')[1].split('=')[1]
                        ret.append(tenhou_id)
                    except IndexError:
                        logging.warning(f"Failed to extract Tenhou ID from line: {line.strip()}")
                        continue
    except Exception as e:
        logging.error(f"Error reading gz file {gz_path}: {e}")
    return ret

def proc_gz(mjai_reviewer_path, gz_path, output_base_dir, max_workers=4):
    """
    处理单个 .gz 文件：解压、提取 Tenhou ID、转换为 mjai JSON 并保存。
    """
    date_str = Path(gz_path).stem  # 获取文件名（去掉路径和扩展名）
    id_list = get_id_list(gz_path)
    if not id_list:
        logging.info(f"No valid Tenhou IDs found in {gz_path}. Skipping.")
        return

    # 构建输出目录路径：tenhou_mjailog/YYYY/YYYYMMDD
    try:
        year = date_str[3:7]
        outdir_path = Path(output_base_dir) / year / date_str[3:]
        outdir_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logging.error(f"Failed to create output directory for {gz_path}: {e}")
        return

    # 使用线程池并行处理 Tenhou IDs
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(get_mjai_log, mjai_reviewer_path, tenhou_id): tenhou_id
            for tenhou_id in id_list
        }

        # 使用 tqdm 进度条显示转换进度
        for future in tqdm(as_completed(future_to_id), total=len(future_to_id), desc=f"Converting {Path(gz_path).name}"):
            tenhou_id = future_to_id[future]
            mjai_log = future.result()
            if mjai_log:
                outfile_path = outdir_path / f"{tenhou_id}.json"
                try:
                    with open(outfile_path, "w", encoding='utf-8') as f:
                        f.write(mjai_log)
                    logging.info(f"Successfully converted and saved {tenhou_id} to {outfile_path}")
                except Exception as e:
                    logging.error(f"Failed to write mjai log for ID {tenhou_id} to {outfile_path}: {e}")

def proc_year(mjai_reviewer_path, year, input_base_dir, output_base_dir, max_workers=4):
    """
    处理指定年份的所有 .gz 文件。
    """
    pattern = f"{input_base_dir}/{year}/scc*.html.gz"
    gz_list = glob.glob(pattern)
    if not gz_list:
        logging.warning(f"No .gz files found for year {year} with pattern {pattern}")
        return

    logging.info(f"Processing {len(gz_list)} .gz files for year {year}")
    for gz_path in gz_list[:1]:
        logging.info(f"Processing file: {gz_path}")
        proc_gz(mjai_reviewer_path, gz_path, output_base_dir, max_workers)

def main():
    setup_logging()  # 初始化日志配置

    parser = argparse.ArgumentParser(description="自动化转换天凤牌谱为 mjai 格式的脚本")
    parser.add_argument(
        "-m", "--mjai-reviewer",
        type=str,
        required=True,
        help="mjai-reviewer.exe 的路径"
    )
    parser.add_argument(
        "-i", "--input-dir",
        type=str,
        required=True,
        help="包含天凤原始 .gz 文件的输入目录"
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default="tenhou_mjailog",
        help="转换后的 mjai JSON 文件保存的基目录"
    )
    parser.add_argument(
        "-y", "--year",
        type=int,
        nargs='*',
        default=[],
        help="要处理的年份列表（例如 2023 2024），如果不指定则处理所有年份"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=4,
        help="每个 .gz 文件处理的最大并行线程数"
    )
    args = parser.parse_args()

    mjai_reviewer_path = Path(args.mjai_reviewer)
    if not mjai_reviewer_path.is_file():
        logging.error(f"mjai-reviewer.exe 未找到: {mjai_reviewer_path}")
        return

    input_base_dir = Path(args.input_dir)
    if not input_base_dir.is_dir():
        logging.error(f"输入目录不存在: {input_base_dir}")
        return

    output_base_dir = Path(args.output_dir)
    output_base_dir.mkdir(parents=True, exist_ok=True)

    # 获取所有年份
    if args.year:
        years = args.year
    else:
        # 如果未指定年份，则自动检测输入目录中的年份子目录
        years = [int(d.name) for d in input_base_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        if not years:
            logging.error(f"未指定年份且输入目录中不存在年份子目录: {input_base_dir}")
            return

    logging.info(f"Starting conversion for years: {years}")
    for year in years:
        proc_year(mjai_reviewer_path, year, input_base_dir, output_base_dir, args.workers)

    logging.info("Conversion process completed.")

if __name__ == "__main__":
    main()
