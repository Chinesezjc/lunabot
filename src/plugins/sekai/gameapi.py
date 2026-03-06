from ..utils import *
from .common import *
from .handler import *

gameapi_config = Config('sekai.gameapi')

@dataclass
class GameApiConfig:
    api_status_url: Optional[str] = None
    profile_api_url: Optional[str] = None 
    suite_api_url: Optional[str] = None
    live_records_api_url: Optional[str] = None
    mysekai_api_url: Optional[str] = None  
    mysekai_photo_api_url: Optional[str] = None 
    mysekai_upload_time_api_url: Optional[str] = None 
    update_msr_sub_api_url: Optional[str] = None
    ranking_api_url: Optional[str] = None
    send_boost_api_url: Optional[str] = None
    create_account_api_url: Optional[str] = None
    ad_result_update_time_api_url: Optional[str] = None
    ad_result_api_url: Optional[str] = None


# 获取游戏api相关配置
def get_gameapi_config(ctx: SekaiHandlerContext) -> GameApiConfig:
    return GameApiConfig(**(gameapi_config.get(ctx.region, {})))


# 请求游戏API data_type: json/bytes/None
async def request_gameapi(url: str, method: str = 'GET', data_type: str | None = 'json', **kwargs):
    # logger.debug(f"请求游戏API后端: {method} {url}")
    token = config.get('gameapi_token', '')
    headers = { 'Authorization': f'Bearer {token}' }
    try:
        async with get_client_session().request(method, url, headers=headers, verify_ssl=False, **kwargs) as resp:
            if resp.status != 200:
                try:
                    detail = await resp.text()
                    detail = loads_json(detail)['detail']
                except:
                    pass
                utils_logger.error(f"请求游戏API后端 {url} 失败: {resp.status} {detail}")
                raise HttpError(resp.status, detail)
            
            if data_type is None:
                return resp
            elif data_type == 'json':
                if "text/plain" in resp.content_type:
                    return loads_json(await resp.text())
                elif "application/octet-stream" in resp.content_type:
                    import io
                    return loads_json(io.BytesIO(await resp.read()).read())
                else:
                    return await resp.json()
            elif data_type == 'bytes':
                return await resp.read()
            else:
                raise Exception(f"不支持的数据类型: {data_type}")
                
    except aiohttp.ClientConnectionError as e:
        raise Exception(f"连接游戏API后端失败，请稍后再试")
