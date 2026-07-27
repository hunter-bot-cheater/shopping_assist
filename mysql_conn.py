from sqlalchemy import create_engine

from config import *


engine=create_engine(

    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"

)


def test_mysql():

    conn=engine.connect()

    print("MySQL连接成功")

    conn.close()



if __name__=="__main__":

    test_mysql()
