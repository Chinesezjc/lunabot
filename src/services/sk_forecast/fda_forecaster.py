import pandas as pd
import numpy as np
from scipy.optimize import curve_fit
from scipy.spatial.distance import euclidean
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')


class FDAForecaster:
    def __init__(self, start_phase_hours: int = 12, end_phase_hours: int = 12, n_neighbors: int = 5):
        self.start_phase_hours = start_phase_hours
        self.end_phase_hours = end_phase_hours
        self.n_neighbors = n_neighbors
        self.history_profiles = [] # {'norm_velocity': np.array, 'weekday_factors': dict, 'avg_velocity': float}
        
    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        data = df.copy()
        data['date'] = data['timestamp'].apply(datetime.fromtimestamp)
        data = data.sort_values('from_start_hour')
        data = data.drop_duplicates(subset=['from_start_hour'], keep='last')

        event_start = None
        if len(data) > 0 and pd.notna(data['date'].iloc[0]):
            event_start = data['date'].iloc[0] - pd.Timedelta(hours=float(data['from_start_hour'].iloc[0]))
        
        # 重采样到1小时粒度
        data.index = pd.to_timedelta(data['from_start_hour'], unit='h')
        resampled = data.resample('1H').agg({
            'score': 'max',
            'to_end_hour': 'mean',
            'date': 'first'
        })
        
        # 插值处理缺失
        resampled['score'] = resampled['score'].interpolate(method='linear')
        resampled['from_start_hour'] = resampled.index.total_seconds() / 3600.0
        
        # 重新计算 to_end_hour 以防中间缺失导致数据不对
        if len(resampled) > 0:
            total_duration = resampled['from_start_hour'].iloc[-1] + resampled['to_end_hour'].iloc[-1]
            resampled['to_end_hour'] = total_duration - resampled['from_start_hour']
        
        # 补充时间特征
        if event_start is not None:
            resampled['date'] = [event_start + pd.Timedelta(hours=h) for h in resampled['from_start_hour']]
            
        resampled['weekday'] = resampled['date'].dt.dayofweek
        resampled['hour_of_day'] = resampled['date'].dt.hour
        
        # 计算速度
        resampled['velocity'] = resampled['score'].diff().fillna(0)
        if len(resampled) > 0 and resampled['velocity'].iloc[0] == 0:
             resampled.loc[resampled.index[0], 'velocity'] = resampled['score'].iloc[0]
        resampled['velocity'] = resampled['velocity'].clip(lower=0)
        
        return resampled

    def add_history(self, data: pd.DataFrame):
        if data.empty or len(data) < 10:
            return
            
        df = self._preprocess_data(data)
        total_score = df['score'].max()
        duration = df['from_start_hour'].max()
        
        if duration < 12 or total_score == 0: 
            return

        # 1. 归一化：消除量级差异，只保留“形状”
        # 我们存储 归一化后的速度曲线 (Normalized Velocity Profile)
        avg_velocity = total_score / duration
        norm_velocity = df['velocity'] / avg_velocity
        
        # 2. 提取周期性特征 (Residuals after removing trend)
        # 简单起见，我们直接存储原始的归一化速度序列，
        # 在匹配时，我们主要匹配“启动期”的形状。
        profile = {
            'duration': duration,
            'norm_velocity': norm_velocity.values, # numpy array
            'norm_cumulative': (norm_velocity.cumsum()).values, # 归一化累积曲线（用于匹配）
            'avg_velocity': avg_velocity,
            'weekday_map': dict(zip(df['from_start_hour'].astype(int), zip(df['weekday'], df['hour_of_day'])))
        }
        self.history_profiles.append(profile)

    def _find_similar_events(self, current_norm_cumulative: np.array) -> list[dict]:
        current_len = len(current_norm_cumulative)
        scores = []

        for profile in self.history_profiles:
            hist_curve = profile['norm_cumulative']
            
            # 必须保证历史数据长度足够覆盖当前数据，否则无法比较
            if len(hist_curve) < current_len:
                continue
                
            # 截取相同长度进行比较
            hist_segment = hist_curve[:current_len]
            
            # 计算距离 (Euclidean Distance)
            # 也可以尝试 DTW (Dynamic Time Warping) 如果时间轴没有严格对齐
            dist = euclidean(current_norm_cumulative, hist_segment)
            
            # 距离越小越相似
            scores.append((dist, profile))
            
        # 按距离排序
        scores.sort(key=lambda x: x[0])
        
        # 返回前 K 个 profile
        top_k = [item[1] for item in scores[:self.n_neighbors]]
        
        # 如果找不到足够的邻居（比如当前活动比所有历史活动都长），这就需要降级处理
        # 这里退化为使用所有历史活动平均形态，避免后期活动直接失败
        if not top_k:
            return self.history_profiles
        return top_k

    def _generate_weighted_reference(self, neighbors: list[dict], target_duration: int) -> np.array:
        if not neighbors:
            return (
                np.ones(self.start_phase_hours),
                np.ones(self.end_phase_hours),
                {},
                1.0,
            )

        # 这里我们需要处理不同长度的邻居。
        # 策略：分解为 Start, Body, End 进行平均，就像之前的逻辑一样，
        # 但这次只在 K 个邻居内部进行平均。
        
        start_profiles = []
        end_profiles = []
        body_factors = {} # (weekday, hour) -> list of velocities
        
        for p in neighbors:
            vel = p['norm_velocity']
            p_len = len(vel)
            
            # Start
            if p_len >= self.start_phase_hours:
                start_profiles.append(vel[:self.start_phase_hours])
            
            # End
            if p_len >= self.end_phase_hours:
                end_profiles.append(vel[-self.end_phase_hours:])
                
            # Body (提取周期性)
            # 使用保存的 weekday_map
            # 这里简化处理：直接取中间段的平均速度作为基准，或者不做复杂的周期性合成，
            # 而是直接对齐邻居的曲线（如果时长相近）。
            # 为了通用性，还是使用分解法：
            
            valid_body_start = self.start_phase_hours
            valid_body_end = p_len - self.end_phase_hours
            
            if valid_body_end > valid_body_start:
                body_vel = vel[valid_body_start:valid_body_end]
                for i, v in enumerate(body_vel):
                    # 获取该小时对应的 weekday/hour
                    t_idx = valid_body_start + i
                    if t_idx in p['weekday_map']:
                        w, h = p['weekday_map'][t_idx]
                        if (w, h) not in body_factors:
                            body_factors[(w, h)] = []
                        body_factors[(w, h)].append(v)

        # 合成 Start
        if start_profiles:
            ref_start = np.mean(start_profiles, axis=0)
        else:
            ref_start = np.ones(self.start_phase_hours)
            
        # 合成 End
        if end_profiles:
            ref_end = np.mean(end_profiles, axis=0)
        else:
            ref_end = np.ones(self.end_phase_hours)
            
        # 合成 Body 查找表
        avg_body_factors = {}
        all_vals = []
        for k, v in body_factors.items():
            val = np.mean(v)
            avg_body_factors[k] = val
            all_vals.append(val)
        global_avg = np.mean(all_vals) if all_vals else 1.0
        
        return ref_start, ref_end, avg_body_factors, global_avg

    def forecast(self, current_data: pd.DataFrame) -> pd.DataFrame:
        if not self.history_profiles:
            raise ValueError("No history profiles available for forecasting.")

        # 1. 预处理当前数据
        df_clean = self._preprocess_data(current_data)
        if df_clean.empty:
            raise ValueError("No valid data for forecasting.")

        # 2. 准备基础元数据
        last_row = df_clean.iloc[-1]
        # 当前已过的小时数（取整，作为切分点）
        current_passed_idx = int(round(last_row['from_start_hour']))
        remaining_hours_est = last_row['to_end_hour']
        
        # 计算活动总时长（向上取整）
        total_duration = int(np.round(last_row['from_start_hour'] + remaining_hours_est))
        
        # 如果活动已经结束，直接返回空
        if current_passed_idx >= total_duration - 1:
            raise ValueError("No future to forecast.")
        
        # 推算活动开始的绝对时间（用于生成时间轴）
        start_date = df_clean['date'].iloc[0] - pd.Timedelta(hours=df_clean['from_start_hour'].iloc[0])

        # 3. 准备归一化所需的数据
        current_score = df_clean['score'].values
        # 简单估算当前均速，用于KNN匹配时的归一化
        curr_avg_vel = current_score[-1] / (current_passed_idx + 1) if current_passed_idx >= 0 else 1
        curr_norm_cumulative = current_score / curr_avg_vel
        
        # 4. 【KNN】寻找相似的历史活动
        # 数据点少于6个时，使用全量历史平均（冷启动）
        if current_passed_idx > 6:
            neighbors = self._find_similar_events(curr_norm_cumulative)
        else:
            neighbors = self.history_profiles

        # 5. 【FDA】生成参考形态 (Reference Shape)
        ref_start, ref_end, body_map, body_base = self._generate_weighted_reference(neighbors, total_duration)
        
        # 构建完整的参考速度曲线
        timeline = [start_date + pd.Timedelta(hours=i) for i in range(total_duration)]
        ref_velocity_curve = np.zeros(total_duration)
        
        # 填充平稳期
        for i, t in enumerate(timeline):
            w, h = t.dayofweek, t.hour
            ref_velocity_curve[i] = body_map.get((w, h), body_base)
            
        # 覆盖启动期
        head_len = min(len(ref_start), total_duration)
        if head_len > 0:
            ref_velocity_curve[:head_len] = ref_start[:head_len]
        
        # 覆盖冲刺期
        tail_len = min(len(ref_end), total_duration)
        if tail_len > 0:
            ref_velocity_curve[-tail_len:] = ref_end[-tail_len:]
            
        # 积分得到理论累积曲线
        ref_cumulative = np.cumsum(ref_velocity_curve)
        
        # 6. 【Fitting】拟合趋势系数 K
        obs_len = min(len(ref_cumulative), len(current_score))
        y_obs = current_score[:obs_len]
        x_ref = ref_cumulative[:obs_len]
        
        # 加权最小二乘：近期权重更高
        weights = np.linspace(0.5, 2.0, len(y_obs))
        
        def func(x, k): 
            return k * x
        
        try:
            popt, _ = curve_fit(func, x_ref, y_obs, sigma=1/weights, absolute_sigma=False)
            scale_k = popt[0]
        except:
            scale_k = y_obs[-1] / x_ref[-1] if x_ref[-1] > 0 else 0
            
        # 7. 【Bias Correction】锚点偏差修正
        fitted_current_value = scale_k * x_ref[-1]
        actual_current_value = y_obs[-1]
        bias = actual_current_value - fitted_current_value
        
        # 8. 生成预测曲线
        final_scores = (ref_cumulative * scale_k) + bias
        # 保证单调递增
        final_scores = np.maximum.accumulate(final_scores)

        # 9. 构造并截取未来数据
        # 生成完整的时间索引
        full_hours_from_start = np.arange(total_duration)
        
        # 截取：只保留当前时刻之后的数据
        # current_passed_idx 是当前最后一个已知数据的小时索引
        # 未来预测从 current_passed_idx + 1 开始
        future_mask = full_hours_from_start > current_passed_idx
        
        if not np.any(future_mask):
             return pd.DataFrame(columns=['score', 'from_start_hour', 'to_end_hour', 'date'])

        future_scores = final_scores[future_mask]
        future_hours = full_hours_from_start[future_mask]
        future_dates = np.array(timeline)[future_mask] # timeline是list，转array方便切片
        
        # 构造结果 DataFrame
        result_df = pd.DataFrame({
            'score': future_scores,
            'from_start_hour': future_hours,
            'to_end_hour': total_duration - future_hours,
            'timestamp': [int(d.to_pydatetime().timestamp()) for d in future_dates]
        })
        return result_df[['score', 'from_start_hour', 'to_end_hour', 'timestamp']]
