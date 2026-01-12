import json
import os
from openai import OpenAI
from datetime import datetime
import pandas as pd
import time
import random

class LLMAnalyzer:
    def __init__(self, api_key="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"):
        """初始化LLM分析器"""
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        
        # 参考路径设置
        self.ref_dir = "/opt/exp/llm/ref"
        self.results_dir = "/opt/exp/llm/data/compositetest"
        os.makedirs(self.results_dir, exist_ok=True)
        
        # 支持的模型列表
        self.supported_models = {
            # "deepseek": "deepseek/deepseek-chat-v3.1:free",
            # "deepseek": "deepseek/deepseek-r1:free",
            "gpt5": "openai/gpt-5",
            "deepseek": "deepseek/deepseek-chat-v3-0324",
            # "claude-sonnet": "anthropic/claude-3-sonnet-20240229",
            "claude4": "anthropic/claude-sonnet-4",
            "claude-haiku": "anthropic/claude-3-haiku-20240307", 
            "gpt4o-mini": "openai/gpt-4o-mini",
            "gpt4o": "openai/gpt-4o",
            "llama-70b": "meta-llama/llama-3.1-70b-instruct:free",
            "qwen-plus": "qwen/qwen-2.5-72b-instruct",
            "gemini-flash": "google/gemini-flash-1.5"
        }
        
        # 模型特定的延迟配置
        self.model_delays = {
            "deepseek/deepseek-r1:free": {
                "base_delay": 3.0,
                "rate_limit_delay": 60.0,
                "max_retry_delay": 300.0
            },
            "meta-llama/llama-3.1-70b-instruct:free": {
                "base_delay": 2.0,
                "rate_limit_delay": 30.0,
                "max_retry_delay": 180.0
            },
            # 默认配置
            "default": {
                "base_delay": 1.0,
                "rate_limit_delay": 20.0,
                "max_retry_delay": 120.0
            }
        }
        
    def load_prompt(self, file_path):
        """加载系统提示"""
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    
    def load_fewshot(self, file_path):
        """加载few-shot示例"""
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    
    def split_fewshot(self, fewshot_text):
        """分割few-shot输入和输出"""
        parts = fewshot_text.split("Expected Output:")
        fewshot_input = parts[0].strip()
        fewshot_output = parts[1].strip() if len(parts) > 1 else ""
        return fewshot_input, fewshot_output
    
    def load_all_fewshots(self):
        """动态加载所有以'shot'开头的few-shot文件"""
        fewshot_dir = os.path.join(self.ref_dir, "fewshot")
        if not os.path.exists(fewshot_dir):
            raise FileNotFoundError(f"Fewshot目录不存在: {fewshot_dir}")
        
        shot_files = []
        for filename in os.listdir(fewshot_dir):
            if filename.startswith('shot') and filename.endswith('.txt'):
                shot_files.append(filename)
        
        shot_files.sort()
        
        if not shot_files:
            raise FileNotFoundError(f"在 {fewshot_dir} 中没有找到以'shot'开头的文件")
        
        fewshot_pairs = []
        for shot_file in shot_files:
            shot_path = os.path.join(fewshot_dir, shot_file)
            shot_text = self.load_fewshot(shot_path)
            shot_input, shot_output = self.split_fewshot(shot_text)
            fewshot_pairs.append((shot_input, shot_output, shot_file))
        
        return fewshot_pairs
    
    def prepare_experiment_data(self, metrics_file, jobinfo_file):
        """准备实验数据"""
        with open(metrics_file, 'r', encoding='utf-8') as f:
            system_metric = f.read().strip()
        
        with open(jobinfo_file, 'r', encoding='utf-8') as f:
            job_info = f.read().strip()
        
        return system_metric, job_info
    
    def build_user_prompt(self, system_metric, job_info):
        """构建用户提示"""
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
        
        # user_prompt = (
        #     "We are diagnosing abnormal behavior in an HPC cluster.\n"
        #     "The following JSON object contains two types of real-time monitoring data:\n"
        #     f"{json_string}\n"
        #     "Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.\n"
        #     "Your output must be a valid JSON array, each object must contain \"anomaly\", \"root_causes\", and \"solutions\". Do not add any explanation or extra text. The output must be directly parsable by Python's json.loads().\n"
        #     "Your output must start with '[' and end with ']'. Do not include any code block markers or extra text."
        # )
        user_prompt = (
            "We are diagnosing abnormal behavior in an HPC cluster.\n"
            "The following JSON object contains two types of real-time monitoring data:\n"
            f"{json_string}\n"
            "Please analyze this data and generate a diagnostic report following the rules and format specified in the System Prompt.\n\n"
            
            "CRITICAL OUTPUT REQUIREMENTS:\n"
            "- Your response must be ONLY a valid JSON array\n"
            "- Start directly with '[' and end with ']'\n"
            "- DO NOT include any markdown code blocks like ```json or ```\n"
            "- DO NOT include any explanatory text before or after the JSON\n"
            "- DO NOT wrap the response in any formatting markers\n"
            "- The output must be directly parsable by Python's json.loads() function\n\n"
            
            "REQUIRED JSON STRUCTURE:\n"
            "Each object in the array must contain exactly these three fields:\n"
            '- "anomaly": string describing the anomaly\n'
            '- "root_causes": string explaining the root causes\n'
            '- "solutions": array of strings with actionable solutions\n\n'
            
            "EXAMPLE OF CORRECT OUTPUT FORMAT:\n"
            '[\n'
            '  {\n'
            '    "anomaly": "Description of the problem",\n'
            '    "root_causes": "Explanation of why it happened",\n'
            '    "solutions": ["Solution 1", "Solution 2", "Solution 3"]\n'
            '  }\n'
            ']\n\n'
            
            "Remember: Your entire response should be ONLY the JSON array, nothing else."
        )

        return user_prompt
    
    def get_model_delay_config(self, model_full_name):
        """获取模型特定的延迟配置"""
        return self.model_delays.get(model_full_name, self.model_delays["default"])
    
    def is_rate_limit_error(self, error_message):
        """检查是否为限流错误"""
        rate_limit_indicators = [
            "429",
            "rate-limited",
            "rate limit",
            "Too Many Requests",
            "quota exceeded",
            "temporarily unavailable"
        ]
        error_str = str(error_message).lower()
        return any(indicator.lower() in error_str for indicator in rate_limit_indicators)
    
    def call_llm_with_adaptive_retry(self, messages, model_full_name, max_retries=5):
        """调用LLM，支持自适应重试机制"""
        delay_config = self.get_model_delay_config(model_full_name)
        base_delay = delay_config["base_delay"]
        rate_limit_delay = delay_config["rate_limit_delay"]
        max_retry_delay = delay_config["max_retry_delay"]
        
        for attempt in range(max_retries):
            try:
                completion = self.client.chat.completions.create(
                    model=model_full_name,
                    messages=messages
                )
                return completion.choices[0].message.content
                
            except Exception as e:
                is_rate_limit = self.is_rate_limit_error(str(e))
                
                if attempt < max_retries - 1:
                    if is_rate_limit:
                        # 限流错误：使用更长的延迟
                        delay = min(rate_limit_delay * (1.5 ** attempt), max_retry_delay)
                        print(f"    尝试 {attempt + 1}/{max_retries} 失败 (限流): {str(e)[:400]}...")
                        print(f"    等待 {delay:.1f} 秒后重试...")
                    else:
                        # 其他错误：使用指数退避
                        delay = min(base_delay * (2 ** attempt), max_retry_delay)
                        print(f"    尝试 {attempt + 1}/{max_retries} 失败: {str(e)[:400]}...")
                        print(f"    等待 {delay:.1f} 秒后重试...")
                    
                    # 添加随机抖动避免雷群效应
                    jitter = random.uniform(0.1, 0.3) * delay
                    total_delay = delay + jitter
                    time.sleep(total_delay)
                else:
                    print(f"    尝试 {attempt + 1}/{max_retries} 失败: {str(e)[:400]}...")
                    raise e
    
    def calculate_dynamic_interval(self, model_full_name, run_id, recent_errors):
        """计算动态间隔时间"""
        delay_config = self.get_model_delay_config(model_full_name)
        base_interval = delay_config["base_delay"]
        
        # 基于最近错误数调整间隔
        error_multiplier = 1 + (recent_errors * 0.5)
        
        # 为免费模型使用更长间隔
        if ":free" in model_full_name:
            base_interval *= 2
        
        # 添加渐进式增长
        progressive_multiplier = 1 + (run_id - 1) * 0.1
        
        final_interval = base_interval * error_multiplier * progressive_multiplier
        
        # 添加随机抖动
        jitter = random.uniform(0.8, 1.2)
        return final_interval * jitter
    
    def clean_llm_response(self, response_text):
        """清理LLM响应，移除代码块标记和多余的文本"""
        if not response_text:
            return response_text
        
        # 移除常见的代码块标记
        cleaned = response_text.strip()
        
        # 移除markdown代码块标记
        if cleaned.startswith('```json'):
            cleaned = cleaned[7:]  # 移除 ```json
        elif cleaned.startswith('```'):
            cleaned = cleaned[3:]   # 移除 ```
        
        if cleaned.endswith('```'):
            cleaned = cleaned[:-3]  # 移除结尾的 ```
        
        # 移除前后的空白字符
        cleaned = cleaned.strip()
        
        # 尝试找到JSON数组的开始和结束
        start_idx = cleaned.find('[')
        end_idx = cleaned.rfind(']')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            # 提取JSON部分
            json_part = cleaned[start_idx:end_idx + 1]
            return json_part
        
        # 如果找不到完整的JSON数组，返回清理后的文本
        return cleaned

    def validate_and_clean_json_response(self, response_text):
        """验证并清理JSON响应"""
        try:
            # 首先尝试直接解析
            parsed = json.loads(response_text)
            return response_text, parsed, None
        except json.JSONDecodeError:
            # 如果失败，尝试清理后解析
            try:
                cleaned = self.clean_llm_response(response_text)
                parsed = json.loads(cleaned)
                return cleaned, parsed, None
            except json.JSONDecodeError as e:
                return response_text, None, str(e)

    def multi_model_experiment_analysis(self, exp_dir, exp_name, models=None, runs_per_model=10):
        """对单个实验使用多个模型进行多次测试"""
        print(f"\n开始多模型分析实验: {exp_name}")
        # exp_002_mem_load: id = 2
        exp_id = int(exp_name.split('_')[1] if '_' in exp_name else 'unknown')
        print(f"id = {exp_id}")
        print("=" * 60)
        
        # 默认使用所有支持的模型
        if models is None:
            models = list(self.supported_models.keys())
        
        # 验证模型名称
        for model in models:
            if model not in self.supported_models:
                raise ValueError(f"不支持的模型: {model}. 支持的模型: {list(self.supported_models.keys())}")
        
        # 创建实验结果目录
        exp_result_dir = os.path.join(self.results_dir, exp_name)
        os.makedirs(exp_result_dir, exist_ok=True)
        
        # 文件路径
        metrics_file = os.path.join(exp_dir, 'metrics.csv')
        jobinfo_file = os.path.join(exp_dir, 'jobinfo.csv')
        fault_info_file = os.path.join(exp_dir, 'fault_info.txt')
        
        if not os.path.exists(metrics_file) or not os.path.exists(jobinfo_file):
            raise FileNotFoundError(f"缺少必要的数据文件 in {exp_dir}")
        
        # 拷贝fault_info.txt
        if os.path.exists(fault_info_file):
            import shutil
            fault_info_dest = os.path.join(exp_result_dir, 'fault_info.txt')
            shutil.copy2(fault_info_file, fault_info_dest)
            print(f"✓ 拷贝fault_info.txt")
        
        # 准备通用数据（所有模型共用）
        system_prompt = self.load_prompt(os.path.join(self.ref_dir, "prompt.md"))
        fewshot_pairs = self.load_all_fewshots()
        system_metric, job_info = self.prepare_experiment_data(metrics_file, jobinfo_file)
        user_prompt = self.build_user_prompt(system_metric, job_info)
        
        # 保存user_prompt.txt（所有模型共用）
        prompt_file = os.path.join(exp_result_dir, 'user_prompt.txt')
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(user_prompt)
        print(f"✓ 保存user_prompt.txt")
        
        # 构建消息列表（所有模型共用）
        messages = [{"role": "system", "content": system_prompt}]
        for shot_input, shot_output, shot_filename in fewshot_pairs:
            messages.append({"role": "user", "content": shot_input})
            messages.append({"role": "assistant", "content": shot_output})
        messages.append({"role": "user", "content": user_prompt})
        
        print(f"使用 {len(fewshot_pairs)} 个few-shot示例")
        print()
        
        # 存储所有结果
        all_results = {}
        
        # 对每个模型进行测试
        for model_short_name in models:
            model_full_name = self.supported_models[model_short_name]
            print(f"开始测试模型: {model_short_name} ({model_full_name})")
            
            # 创建模型专属目录
            model_dir = os.path.join(exp_result_dir, model_short_name)
            os.makedirs(model_dir, exist_ok=True)
            
            # 保存模型完整名称
            model_info_file = os.path.join(model_dir, 'model_name')
            with open(model_info_file, 'w', encoding='utf-8') as f:
                f.write(model_full_name)
            
            model_results = []
            successful_runs = 0
            recent_errors = 0  # 跟踪最近的错误数
            
            # 进行多次运行
            for run_id in range(1, runs_per_model + 1):
                print(f"  运行 {run_id}/{runs_per_model}...", end=' ')
                
                try:
                    # 调用LLM
                    # %TEMP%
                    if model_short_name == "deepseek" and exp_id == 1:
                        print("Allready test, continue")
                        continue
                    llm_response = self.call_llm_with_adaptive_retry(messages, model_full_name)
                    
                    # 保存原始响应
                    response_file = os.path.join(model_dir, f'llm_response_{run_id:02d}.txt')
                    with open(response_file, 'w', encoding='utf-8') as f:
                        f.write(llm_response)
                    
                    # 验证和清理JSON格式
                    cleaned_response, parsed_response, error = self.validate_and_clean_json_response(llm_response)
                    
                    if parsed_response is not None:
                        anomaly_count = len(parsed_response)
                        print(f"✓ (检测到 {anomaly_count} 个异常)")
                        
                        # 如果响应被清理过，保存清理后的版本
                        if cleaned_response != llm_response:
                            cleaned_file = os.path.join(model_dir, f'llm_response_{run_id:02d}_cleaned.txt')
                            with open(cleaned_file, 'w', encoding='utf-8') as f:
                                f.write(cleaned_response)
                        
                        model_results.append({
                            "run_id": run_id,
                            "success": True,
                            "anomaly_count": anomaly_count,
                            "response_file": f'llm_response_{run_id:02d}.txt',
                            "cleaned_file": f'llm_response_{run_id:02d}_cleaned.txt' if cleaned_response != llm_response else None,
                            "was_cleaned": cleaned_response != llm_response,
                            "timestamp": datetime.now().isoformat()
                        })
                        successful_runs += 1
                        recent_errors = max(0, recent_errors - 1)
                        
                    else:
                        print(f"⚠️ JSON格式错误: {error[:50]}...")
                        
                        # 保存错误分析
                        error_file = os.path.join(model_dir, f'llm_response_{run_id:02d}_error.txt')
                        with open(error_file, 'w', encoding='utf-8') as f:
                            f.write(f"Original Response:\n{llm_response}\n\n")
                            f.write(f"Cleaned Response:\n{cleaned_response}\n\n")
                            f.write(f"JSON Error: {error}\n")
                        
                        model_results.append({
                            "run_id": run_id,
                            "success": False,
                            "error": error,
                            "response_file": f'llm_response_{run_id:02d}.txt',
                            "error_file": f'llm_response_{run_id:02d}_error.txt',
                            "timestamp": datetime.now().isoformat()
                        })
                        recent_errors += 1
                    
                except Exception as e:
                    print(f"❌ 调用失败: {str(e)[:50]}...")
                    model_results.append({
                        "run_id": run_id,
                        "success": False,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    })
                    recent_errors += 1
                
                # 计算动态间隔时间（如果不是最后一次运行）
                if run_id < runs_per_model:
                    interval = self.calculate_dynamic_interval(model_full_name, run_id, recent_errors)
                    print(f"    等待 {interval:.1f} 秒...")
                    time.sleep(interval)
            
            # 保存模型结果统计
            model_summary = {
                "model_short_name": model_short_name,
                "model_full_name": model_full_name,
                "total_runs": runs_per_model,
                "successful_runs": successful_runs,
                "success_rate": f"{successful_runs/runs_per_model*100:.1f}%",
                "results": model_results
            }
            
            summary_file = os.path.join(model_dir, 'model_summary.json')
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(model_summary, f, indent=2, ensure_ascii=False)
            
            all_results[model_short_name] = model_summary
            print(f"  ✓ 模型 {model_short_name} 完成: {successful_runs}/{runs_per_model} 成功")
            print()
            
            # 模型之间的间隔
            if model_short_name != models[-1]:  # 不是最后一个模型
                inter_model_delay = 5.0
                print(f"模型间等待 {inter_model_delay} 秒...")
                time.sleep(inter_model_delay)
        
        # 保存实验总结
        experiment_summary = {
            "experiment_name": exp_name,
            "timestamp": datetime.now().isoformat(),
            "models_tested": list(models),
            "runs_per_model": runs_per_model,
            "fewshot_files_used": [shot_filename for _, _, shot_filename in fewshot_pairs],
            "results": all_results
        }
        
        exp_summary_file = os.path.join(exp_result_dir, 'experiment_summary.json')
        with open(exp_summary_file, 'w', encoding='utf-8') as f:
            json.dump(experiment_summary, f, indent=2, ensure_ascii=False)
        
        print(f"✓ 实验 {exp_name} 完成！")
        print(f"✓ 结果保存在: {exp_result_dir}")
        
        return experiment_summary
    
    def batch_multi_model_analysis(self, experiments_dir, models=None, runs_per_model=10, max_experiments=7):
        """批量进行多模型分析（限制前N个实验）"""
        print(f"开始批量多模型分析...")
        print(f"实验目录: {experiments_dir}")
        print(f"测试模型: {models if models else list(self.supported_models.keys())}")
        print(f"每模型运行次数: {runs_per_model}")
        print(f"最大实验数: {max_experiments}")
        print("=" * 80)
        
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
        
        # 限制实验数量
        if max_experiments:
            exp_dirs = exp_dirs[:max_experiments]
        
        print(f"将测试前 {len(exp_dirs)} 个实验")
        print()
        
        all_experiment_results = []
        
        for i, (exp_name, exp_path) in enumerate(exp_dirs, 1):
            print(f"[{i}/{len(exp_dirs)}] 处理实验: {exp_name}")
            
            try:
                result = self.multi_model_experiment_analysis(
                    exp_path, exp_name, models, runs_per_model
                )
                all_experiment_results.append(result)
                
            except Exception as e:
                print(f"❌ 实验 {exp_name} 失败: {e}")
                import traceback
                print(f"详细错误: {traceback.format_exc()}")
            
            # 实验之间的间隔
            if i < len(exp_dirs):
                inter_exp_delay = 10.0
                print(f"实验间等待 {inter_exp_delay} 秒...")
                time.sleep(inter_exp_delay)
                print()
        
        # 生成批量分析总结
        batch_summary = {
            "batch_analysis_time": datetime.now().isoformat(),
            "total_experiments": len(exp_dirs),
            "successful_experiments": len(all_experiment_results),
            "models_tested": models if models else list(self.supported_models.keys()),
            "runs_per_model": runs_per_model,
            "experiments": all_experiment_results
        }
        
        batch_summary_file = os.path.join(self.results_dir, "batch_multi_model_summary.json")
        with open(batch_summary_file, 'w', encoding='utf-8') as f:
            json.dump(batch_summary, f, indent=2, ensure_ascii=False)
        
        print("=" * 80)
        print(f"✓ 批量多模型分析完成！")
        print(f"✓ 成功处理: {len(all_experiment_results)}/{len(exp_dirs)} 个实验")
        print(f"✓ 批量总结: {batch_summary_file}")
        
        return batch_summary


def main():
    """主函数"""
    # 初始化分析器
    analyzer = LLMAnalyzer()
    datadir = "/opt/exp/dataset_builder/data/compositetest/extracted_data"
    
    # 定义要测试的模型（可以根据需要调整）
    test_models = [
        # "deepseek",
        "gpt5",
        # "claude4", 
        # "gpt4o-mini",
        # "llama-70b"
    ]
    
    # 批量多模型分析前7个实验，每个模型运行10次
    results = analyzer.batch_multi_model_analysis(
        experiments_dir=datadir,
        models=test_models,
        runs_per_model=10,
        max_experiments=7
    )


if __name__ == "__main__":
    main()