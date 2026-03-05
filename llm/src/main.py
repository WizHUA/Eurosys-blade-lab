import json
import os
from openai import OpenAI
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class LLMAnalyzer:
    def __init__(self, api_key=None):
        """初始化LLM分析器"""
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("未提供API Key，请设置 OPENROUTER_API_KEY 环境变量或在初始化时传入。")

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
        )
        
        # 获取当前脚本所在目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 项目根目录 (假设 src 在 llm 下，llm 在根目录下)
        project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
        
        # 参考路径设置 (使用相对路径)
        self.ref_dir = os.path.join(project_root, "llm", "ref")
        self.results_dir = os.path.join(project_root, "llm", "data", "formaltest")
        os.makedirs(self.results_dir, exist_ok=True)
        
    def load_prompt(self, file_path):
        """加载系统提示"""
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    
    def load_fewshot(self, file_path):
        """加载few-shot示例"""
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    
    def split_fewshot(self, fewshot_text):
        """分割few-shot输入和输出 - 完全按照run.py的逻辑"""
        parts = fewshot_text.split("Expected Output:")
        fewshot_input = parts[0].strip()
        fewshot_output = parts[1].strip() if len(parts) > 1 else ""
        return fewshot_input, fewshot_output
    
    def load_all_fewshots(self):
        """动态加载所有以'shot'开头的few-shot文件"""
        fewshot_dir = os.path.join(self.ref_dir, "fewshot")
        if not os.path.exists(fewshot_dir):
            raise FileNotFoundError(f"Fewshot目录不存在: {fewshot_dir}")
        
        # 获取所有以'shot'开头的文件
        shot_files = []
        for filename in os.listdir(fewshot_dir):
            if filename.startswith('shot') and filename.endswith('.txt'):
                shot_files.append(filename)
        
        # 按文件名排序以确保顺序一致
        shot_files.sort()
        
        if not shot_files:
            raise FileNotFoundError(f"在 {fewshot_dir} 中没有找到以'shot'开头的文件")
        
        print(f"  发现 {len(shot_files)} 个few-shot文件: {shot_files}")
        
        # 加载并分割所有few-shot文件
        fewshot_pairs = []
        for shot_file in shot_files:
            shot_path = os.path.join(fewshot_dir, shot_file)
            shot_text = self.load_fewshot(shot_path)
            shot_input, shot_output = self.split_fewshot(shot_text)
            fewshot_pairs.append((shot_input, shot_output, shot_file))
        
        return fewshot_pairs
    
    def get_in_period_job(self, metric_content, job_content):
        """过滤与metrics时间段有交集的作业 - 完全按照run.py的逻辑"""
        lines = metric_content.strip().split('\n')
        if len(lines) < 2:
            return ""
        first_metric_time = lines[1].split(',')[0]
        last_metric_time = lines[-1].split(',')[0]

        metric_start = datetime.strptime(first_metric_time, "%Y-%m-%d %H:%M:%S")
        metric_end = datetime.strptime(last_metric_time, "%Y-%m-%d %H:%M:%S")

        job_lines = job_content.strip().split('\n')
        header = job_lines[0]
        filtered_jobs = [header]

        for line in job_lines[1:]:
            if not line.strip():  # 跳过空行
                continue
            parts = line.split('|')
            if len(parts) < 6:  # 确保有足够的列
                continue
            job_start_str = parts[4]
            job_end_str = parts[5]
            try:
                job_start = datetime.strptime(job_start_str, "%Y-%m-%dT%H:%M:%S")
                job_end = datetime.strptime(job_end_str, "%Y-%m-%dT%H:%M:%S")
                if job_start <= metric_end and job_end >= metric_start:
                    filtered_jobs.append(line)
            except ValueError:
                # 时间格式解析失败，跳过该作业
                continue

        return '\n'.join(filtered_jobs)
    
    def prepare_experiment_data(self, metrics_file, jobinfo_file):
        """准备实验数据 - 完全按照run.py的格式"""
        # 读取metrics数据
        with open(metrics_file, 'r', encoding='utf-8') as f:
            system_metric = f.read().strip()
        
        # 读取jobinfo数据
        with open(jobinfo_file, 'r', encoding='utf-8') as f:
            job_info = f.read().strip()
        
        # 过滤相关作业（按照run.py的逻辑）
        # filtered_job = self.get_in_period_job(system_metric, job_info)
        
        return system_metric, job_info # 数据已经处理过了
    
    def build_user_prompt(self, system_metric, job_info):
        """构建用户提示 - 完全按照shot1.txt的格式，数据行需要缩进"""
        
        # 为metrics数据添加适当的缩进
        metrics_lines = system_metric.split('\n')
        formatted_metrics_lines = []
        for i, line in enumerate(metrics_lines):
            if i == 0:  # 第一行（表头）不缩进
                formatted_metrics_lines.append(line)
            else:  # 数据行需要缩进
                formatted_metrics_lines.append(f"                                   {line}")
        formatted_metrics = '\n'.join(formatted_metrics_lines)
        
        # 为job_info数据添加适当的缩进
        job_lines = job_info.split('\n')
        formatted_job_lines = []
        for i, line in enumerate(job_lines):
            if i == 0:  # 第一行（表头）不缩进
                formatted_job_lines.append(line)
            else:  # 数据行需要缩进
                formatted_job_lines.append(f"                    {line}")
        formatted_jobs = '\n'.join(formatted_job_lines)
        
        # 构建格式化的JSON字符串
        json_string = (
            "{\n"
            f'  "system_metrics": "{formatted_metrics}",\n'
            f'  "job_status": "{formatted_jobs}"\n'
            "}"
        )
        
        user_prompt = (
            "We are diagnosing abnormal behavior in an HPC cluster.\n"
            "The following JSON object contains two types of real-time monitoring data:\n"
            f"{json_string}\n"
            "Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.\n"
            "Your output must be a valid JSON array, each object must contain \"anomaly\", \"root_causes\", and \"solutions\". Do not add any explanation or extra text. The output must be directly parsable by Python's json.loads().\n"
            "Your output must start with '[' and end with ']'. Do not include any code block markers or extra text."
        )

        return user_prompt
    
    def analyze_experiment(self, exp_dir, exp_name, model="deepseek/deepseek-chat-v3.1:free"):
        """分析单个实验 - 完全按照run.py的test2()逻辑"""
        print(f"开始分析实验: {exp_name}")
        
        # 文件路径
        metrics_file = os.path.join(exp_dir, 'metrics.csv')
        jobinfo_file = os.path.join(exp_dir, 'jobinfo.csv')
        
        if not os.path.exists(metrics_file) or not os.path.exists(jobinfo_file):
            raise FileNotFoundError(f"缺少必要的数据文件 in {exp_dir}")
        
        try:
            # 1. 加载系统提示 - 按照run.py路径
            system_prompt = self.load_prompt(os.path.join(self.ref_dir, "prompt.md"))
            
            # 2. 动态加载所有few-shot示例
            fewshot_pairs = self.load_all_fewshots()
            
            # 3. 准备实验数据
            system_metric, job_info = self.prepare_experiment_data(metrics_file, jobinfo_file)
            
            # 4. 构建用户提示
            user_prompt = self.build_user_prompt(system_metric, job_info)
            
            # 5. 构建消息列表 - 动态添加所有few-shot示例
            messages = [{"role": "system", "content": system_prompt}]
            
            # 添加所有few-shot示例
            for shot_input, shot_output, shot_filename in fewshot_pairs:
                messages.append({"role": "user", "content": shot_input})
                messages.append({"role": "assistant", "content": shot_output})
            
            # 添加当前用户提示
            messages.append({"role": "user", "content": user_prompt})
            
            print(f"  使用 {len(fewshot_pairs)} 个few-shot示例")
            
            # 6. 调用LLM
            completion = self.client.chat.completions.create(
                model=model,
                messages=messages
            )
            
            # 7. 获取响应
            llm_response = completion.choices[0].message.content
            
            # 8. 保存结果
            result = {
                "experiment_name": exp_name,
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "fewshot_files_used": [shot_filename for _, _, shot_filename in fewshot_pairs],
                "user_prompt": user_prompt,
                "llm_response": llm_response,
                "raw_data": {
                    "system_metrics": system_metric,
                    "job_info": job_info
                }
            }
            
            # 保存到文件
            result_file = os.path.join(self.results_dir, f"{exp_name}_result.json")
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            print(f"  ✓ 分析完成，结果保存到: {result_file}")

            # 分别保存user_prompt和llm_response
            prompt_file = os.path.join(self.results_dir, f"{exp_name}_user_prompt.txt")
            with open(prompt_file, 'w', encoding='utf-8') as f:
                f.write(user_prompt)
            print(f"  ✓ 用户提示保存到: {prompt_file}")
            response_file = os.path.join(self.results_dir, f"{exp_name}_llm_response.txt")
            with open(response_file, 'w', encoding='utf-8') as f:
                f.write(llm_response)
            print(f"  ✓ LLM响应保存到: {response_file}")
            
            # 尝试解析JSON响应（按照run.py的逻辑）
            try:
                parsed_response = json.loads(llm_response)
                print(f"  ✓ LLM响应格式正确，检测到 {len(parsed_response)} 个异常")
            except json.JSONDecodeError as e:
                print(f"  ⚠️ LLM响应JSON格式错误: {e}")
                # 保存原始响应以便调试
                raw_file = os.path.join(self.results_dir, f"{exp_name}_raw_response.txt")
                with open(raw_file, 'w', encoding='utf-8') as f:
                    f.write(llm_response)
                print(f"  ✓ 原始响应保存到: {raw_file}")
            
            return result
            
        except Exception as e:
            print(f"  ❌ 分析失败: {e}")
            return None
    
    def batch_analyze(self, experiments_dir, model="deepseek/deepseek-chat-v3.1:free"):
        """批量分析实验"""
        print(f"开始批量分析实验...")
        print(f"实验目录: {experiments_dir}")
        print(f"使用模型: {model}")
        print("=" * 60)
        
        if not os.path.exists(experiments_dir):
            print(f"❌ 实验目录不存在: {experiments_dir}")
            return
        
        # 获取所有实验目录
        exp_dirs = []
        for item in os.listdir(experiments_dir):
            item_path = os.path.join(experiments_dir, item)
            if os.path.isdir(item_path) and item.startswith('exp_'):
                exp_dirs.append((item, item_path))
        
        exp_dirs.sort()  # 按名称排序
        
        print(f"找到 {len(exp_dirs)} 个实验目录")
        print()
        
        results = []
        success_count = 0
        
        for exp_name, exp_path in exp_dirs:
            print(f"开始分析实验: {exp_name}")
            
            try:
                # 创建该实验的结果目录
                exp_result_dir = os.path.join(self.results_dir, exp_name)
                os.makedirs(exp_result_dir, exist_ok=True)
                
                # 文件路径
                metrics_file = os.path.join(exp_path, 'metrics.csv')
                jobinfo_file = os.path.join(exp_path, 'jobinfo.csv')
                fault_info_file = os.path.join(exp_path, 'fault_info.txt')
                
                if not os.path.exists(metrics_file) or not os.path.exists(jobinfo_file):
                    print(f"  ❌ 缺少必要的数据文件")
                    continue
                
                # 1. 加载系统提示和few-shot示例
                system_prompt = self.load_prompt(os.path.join(self.ref_dir, "prompt.md"))
                fewshot_pairs = self.load_all_fewshots()
                print(f"  使用 {len(fewshot_pairs)} 个few-shot示例")
                
                # 2. 准备实验数据
                system_metric, job_info = self.prepare_experiment_data(metrics_file, jobinfo_file)
                
                # 3. 构建用户提示
                user_prompt = self.build_user_prompt(system_metric, job_info)
                
                # 4. 构建消息列表
                messages = [{"role": "system", "content": system_prompt}]
                
                # 添加所有few-shot示例
                for shot_input, shot_output, shot_filename in fewshot_pairs:
                    messages.append({"role": "user", "content": shot_input})
                    messages.append({"role": "assistant", "content": shot_output})
                
                # 添加当前用户提示
                messages.append({"role": "user", "content": user_prompt})
                
                # 5. 调用LLM
                completion = self.client.chat.completions.create(
                    model=model,
                    messages=messages
                )
                
                # 6. 获取响应
                llm_response = completion.choices[0].message.content
                
                # 7. 创建结果数据
                result = {
                    "experiment_name": exp_name,
                    "timestamp": datetime.now().isoformat(),
                    "model": model,
                    "fewshot_files_used": [shot_filename for _, _, shot_filename in fewshot_pairs],
                    "raw_data": {
                        "system_metrics": system_metric,
                        "job_info": job_info
                    }
                }
                
                # 8. 保存文件到实验专属目录
                
                # 保存 result.json
                result_file = os.path.join(exp_result_dir, 'result.json')
                with open(result_file, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                
                # 保存 user_prompt.txt
                prompt_file = os.path.join(exp_result_dir, 'user_prompt.txt')
                with open(prompt_file, 'w', encoding='utf-8') as f:
                    f.write(user_prompt)
                
                # 保存 llm_response.txt
                response_file = os.path.join(exp_result_dir, 'llm_response.txt')
                with open(response_file, 'w', encoding='utf-8') as f:
                    f.write(llm_response)
                
                # 拷贝 fault_info.txt
                if os.path.exists(fault_info_file):
                    import shutil
                    fault_info_dest = os.path.join(exp_result_dir, 'fault_info.txt')
                    shutil.copy2(fault_info_file, fault_info_dest)
                    print(f"  ✓ 拷贝fault_info.txt")
                else:
                    print(f"  ⚠️ 原始目录中缺少fault_info.txt文件")
                
                print(f"  ✓ 分析完成，结果保存到目录: {exp_result_dir}")
                
                # 验证JSON响应格式
                try:
                    parsed_response = json.loads(llm_response)
                    print(f"  ✓ LLM响应格式正确，检测到 {len(parsed_response)} 个异常")
                except json.JSONDecodeError as e:
                    print(f"  ⚠️ LLM响应JSON格式错误: {e}")
                
                results.append(result)
                success_count += 1
                
            except Exception as e:
                print(f"  ❌ 分析失败: {e}")
                import traceback
                print(f"  详细错误: {traceback.format_exc()}")
            
            print()
        
        # 生成总结报告
        summary = {
            "batch_analysis_time": datetime.now().isoformat(),
            "model_used": model,
            "total_experiments": len(exp_dirs),
            "successful_analyses": success_count,
            "failed_analyses": len(exp_dirs) - success_count,
            "success_rate": f"{success_count/len(exp_dirs)*100:.1f}%" if exp_dirs else "0%",
            "results": [
                {
                    "experiment_name": r["experiment_name"],
                    "timestamp": r["timestamp"],
                    "result_dir": os.path.join(self.results_dir, r["experiment_name"])
                } for r in results
            ]
        }
        
        summary_file = os.path.join(self.results_dir, "batch_analysis_summary.json")
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        print("=" * 60)
        print(f"✓ 批量分析完成！")
        print(f"✓ 成功分析: {success_count}/{len(exp_dirs)} 个实验")
        print(f"✓ 结果目录: {self.results_dir}")
        print(f"✓ 总结报告: {summary_file}")
        print(f"\n实验结果目录结构:")
        for r in results:
            exp_name = r["experiment_name"]
            print(f"  {exp_name}/")
            print(f"    ├── result.json")
            print(f"    ├── user_prompt.txt")
            print(f"    ├── llm_response.txt")
            print(f"    └── fault_info.txt")
        
        return results


def main():
    """主函数 - 示例用法"""
    # 初始化分析器
    analyzer = LLMAnalyzer()
    datadir = "/opt/exp/dataset_builder/data/formaltest/extracted_data"
    
    # 批量分析所有实验
    results = analyzer.batch_analyze(datadir, model="deepseek/deepseek-chat-v3.1:free")

    # # 也可以单独分析某个实验
    # single_result = analyzer.analyze_experiment(
    #     os.path.join(datadir, "exp_001_cpu_fullload"),
    #     "exp_001_cpu_fullload",
    #     model="deepseek/deepseek-chat-v3.1:free"
    # )


if __name__ == "__main__":
    main()