FROM maven:3.9.9-eclipse-temurin-17

RUN sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 curl default-mysql-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/ /app/
# 构建缓存支持源码迭代，镜像内另存完整依赖，供运行时离线编译。
RUN --mount=type=cache,id=sap-order-sync-maven,target=/maven-cache \
    chmod +x /app/scripts/*.sh \
    && mvn -B -Dmaven.repo.local=/maven-cache -DskipTests package \
    && mkdir -p /root/.m2/repository \
    && cp -a /maven-cache/. /root/.m2/repository/
ENV DB_HOST=db DB_PORT=3306 DB_NAME=order_bench DB_USER=bench DB_PASSWORD=local_bench_demo
CMD ["sleep", "infinity"]
