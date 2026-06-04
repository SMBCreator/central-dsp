package io.centraldsp.bidder.config;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import java.time.Duration;

/**
 * Redis connection pool configuration.
 *
 * <p>We use Jedis (not Lettuce) because Jedis has simpler threading semantics
 * for our high-QPS bidder. JedisPool handles connection reuse automatically.</p>
 *
 * <p>Settings are read from application.properties:
 * <ul>
 *   <li>redis.host  (default: redis)</li>
 *   <li>redis.port  (default: 6379)</li>
 *   <li>redis.db    (default: 1)</li>
 * </ul>
 * </p>
 */
@Slf4j
@Configuration
public class RedisConfig {

    @Value("${redis.host:redis}")
    private String host;

    @Value("${redis.port:6379}")
    private int port;

    @Value("${redis.password:}")
    private String password;

    @Value("${redis.db:1}")
    private int database;

    /**
     * Tuned JedisPool for high-throughput RTB bidder.
     *
     * <p>Max 64 connections: enough for 100 Tomcat threads with headroom.
     * Min idle 8: pre-warmed connections to avoid latency spikes.
     * Max wait 50ms: bid responses must arrive in &lt;100ms total.</p>
     */
    @Bean
    public JedisPool jedisPool() {
        JedisPoolConfig config = new JedisPoolConfig();
        config.setMaxTotal(64);
        config.setMaxIdle(16);
        config.setMinIdle(8);
        config.setMaxWait(Duration.ofMillis(50));
        config.setTestOnBorrow(false);   // skip PING on borrow — saves ~1ms
        config.setTestWhileIdle(true);
        config.setTimeBetweenEvictionRuns(Duration.ofSeconds(30));

        String effectivePassword = (password != null && !password.isBlank()) ? password : null;

        JedisPool pool;
        if (effectivePassword != null) {
            pool = new JedisPool(config, host, port, 2000, effectivePassword, database);
        } else {
            pool = new JedisPool(config, host, port, 2000, null, database);
        }

        log.info("[Redis] JedisPool initialized: {}:{} db={} maxTotal={}", host, port, database, 64);
        return pool;
    }
}
