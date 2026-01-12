import csv
import requests
import json
import sys
from urllib.parse import urlencode

def test_prometheus_query(query, metric_name):
    """测试单个Prometheus查询"""
    prometheus_url = "http://localhost:9090"
    
    # 构建查询URL
    params = {
        'query': query
    }
    url = f"{prometheus_url}/api/v1/query?" + urlencode(params)
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        result = response.json()
        
        if result['status'] == 'success':
            data_length = len(result['data']['result'])
            if data_length > 0:
                return True, f"OK - {data_length} results"
            else:
                return False, "No data returned"
        else:
            return False, f"Query failed: {result.get('error', 'Unknown error')}"
            
    except requests.exceptions.RequestException as e:
        return False, f"Connection error: {str(e)}"
    except json.JSONDecodeError:
        return False, "Invalid JSON response"
    except Exception as e:
        return False, f"Error: {str(e)}"

def validate_all_metrics(csv_file):
    """验证CSV文件中的所有指标查询"""
    success_count = 0
    total_count = 0
    failed_queries = []
    
    print(f"正在验证指标查询...")
    print("=" * 80)
    
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            metric_name = row['name']
            query = row['query']
            chinese_name = row['中文含义']
            total_count += 1
            
            print(f"[{total_count:2d}] 测试: {metric_name} ({chinese_name})")
            
            success, message = test_prometheus_query(query, metric_name)
            
            if success:
                print(f"     ✓ {message}")
                success_count += 1
            else:
                print(f"     ✗ {message}")
                failed_queries.append({
                    'name': metric_name,
                    'chinese_name': chinese_name,
                    'query': query,
                    'error': message
                })
            
            print()
    
    print("=" * 80)
    print(f"验证完成: {success_count}/{total_count} 个查询成功")
    
    if failed_queries:
        print(f"\n失败的查询 ({len(failed_queries)}个):")
        print("-" * 60)
        for failed in failed_queries:
            print(f"指标: {failed['name']}")
            print(f"名称: {failed['chinese_name']}")
            print(f"查询: {failed['query']}")
            print(f"错误: {failed['error']}")
            print("-" * 60)
    
    return success_count, total_count, failed_queries

if __name__ == "__main__":
    csv_file = "/opt/exp/note/metric.csv"
    
    print("Prometheus指标查询验证脚本")
    print(f"读取配置文件: {csv_file}")
    
    try:
        success, total, failed = validate_all_metrics(csv_file)
        
        if success == total:
            print(f"\n🎉 所有 {total} 个指标查询都验证成功!")
            sys.exit(0)
        else:
            print(f"\n⚠️  有 {len(failed)} 个指标查询失败，需要检查配置")
            sys.exit(1)
            
    except FileNotFoundError:
        print(f"错误: 找不到文件 {csv_file}")
        sys.exit(1)
    except Exception as e:
        print(f"脚本执行错误: {str(e)}")
        sys.exit(1)