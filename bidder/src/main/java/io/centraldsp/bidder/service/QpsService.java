package io.centraldsp.bidder.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;
import redis.clients.jedis.Pipeline;
import redis.clients.jedis.Response;

import javax.annotation.PostConstruct;
import java.time.Instant;

/**
 * Fixed 1-second window QPS limiter, key-compatible with backend/cache.py:
 * key "qps:{partner_id}:{unix_second}", INCR + EXPIRE 2s pipelined.
 * Sharing the key format means Python and Java replicas enforce one
 * combined limit per partner.
 */
@Slf4j
@Service
public class QpsService {

    @Value("${redis.host:localhost}")
    private String redisHost;

    @Value("${redis.port:6379}")
    private int redisPort;

    private JedisPool pool;

    @PostConstruct
    public void init() {
        JedisPoolConfig config = new JedisPoolConfig();
        config.setMaxTotal(64);
        pool = new JedisPool(config, redisHost, redisPort);
    }

    /** True while the partner is under its QPS limit. limit <= 0 = unlimited. */
    public boolean allow(long partnerId, Integer limit) {
        if (limit == null || limit <= 0) return true;
        String key = "qps:" + partnerId + ":" + Instant.now().getEpochSecond();
        try (Jedis jedis = pool.getResource()) {
            Pipeline p = jedis.pipelined();
            Response<Long> count = p.incr(key);
            p.expire(key, 2);
            p.sync();
            return count.get() <= limit;
        } catch (Exception e) {
            log.warn("[QPS] Redis unavailable, failing open: {}", e.getMessage());
            return true;
        }
    }
}
