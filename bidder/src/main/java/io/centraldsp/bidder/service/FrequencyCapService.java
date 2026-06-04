package io.centraldsp.bidder.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import javax.annotation.PostConstruct;

/**
 * Frequency cap enforcement using Redis INCR counters.
 *
 * <p>Key pattern: {@code cdsp:fcap:{campaignId}:{userId}}</p>
 * <p>TTL = {@code fcap.window.hours} (default 24h).</p>
 * <p>Fail-open: on Redis errors impression is allowed.</p>
 */
@Slf4j
@Service
public class FrequencyCapService {

    @Value("${redis.host:redis}")
    private String redisHost;

    @Value("${redis.port:6379}")
    private int redisPort;

    @Value("${redis.db:1}")
    private int redisDb;

    @Value("${fcap.window.hours:24}")
    private int fcapWindowHours;

    @Value("${fcap.default.limit:10}")
    private int defaultFcapLimit;

    private JedisPool jedisPool;

    private static final String KEY_PREFIX = "cdsp:fcap:";

    @PostConstruct
    public void init() {
        JedisPoolConfig config = new JedisPoolConfig();
        config.setMaxTotal(20);
        config.setMaxIdle(5);
        config.setMinIdle(1);
        jedisPool = new JedisPool(config, redisHost, redisPort);
        log.info("[FrequencyCap] Redis {}:{} db={} window={}h limit={}",
                redisHost, redisPort, redisDb, fcapWindowHours, defaultFcapLimit);
    }

    /**
     * Check whether user is within the frequency cap.
     *
     * @param campaignId     Campaign ID
     * @param userId         User/device identifier
     * @param maxImpressions Max allowed impressions in the window
     * @return true = bid allowed, false = user is capped
     */
    public boolean isWithinCap(Long campaignId, String userId, int maxImpressions) {
        if (userId == null || userId.isBlank()) return true;
        String key = fcapKey(campaignId, userId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            int count = val != null ? Integer.parseInt(val) : 0;
            return count < maxImpressions;
        } catch (Exception e) {
            log.warn("[FrequencyCap] isWithinCap error campaign={} user={}: {}", campaignId, userId, e.getMessage());
            return true;
        }
    }

    /** Overload with default limit from config. */
    public boolean isWithinCap(Long campaignId, String userId) {
        return isWithinCap(campaignId, userId, defaultFcapLimit);
    }

    /**
     * BidEngineService calls this method (canServe is the legacy name).
     * Alias: canServe == isWithinCap with defaultFcapLimit.
     */
    public boolean canServe(Long campaignId, String userId) {
        if (userId == null) return true;
        return isWithinCap(campaignId, userId, defaultFcapLimit);
    }

    /**
     * Record an impression win (called from WinLossController).
     * Atomically increments counter; sets TTL on first call.
     */
    public void recordImpression(Long campaignId, String userId) {
        if (userId == null || userId.isBlank()) return;
        String key = fcapKey(campaignId, userId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            long newCount = jedis.incr(key);
            if (newCount == 1) {
                jedis.expire(key, (long) fcapWindowHours * 3600);
            }
            log.debug("[FrequencyCap] campaign={} user={} count={}", campaignId, userId, newCount);
        } catch (Exception e) {
            log.error("[FrequencyCap] recordImpression error campaign={} user={}: {}", campaignId, userId, e.getMessage());
        }
    }

    /** Get current impression count (debug/monitoring). */
    public int getImpressionCount(Long campaignId, String userId) {
        if (userId == null || userId.isBlank()) return 0;
        String key = fcapKey(campaignId, userId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            return val != null ? Integer.parseInt(val) : 0;
        } catch (Exception e) {
            return 0;
        }
    }

    /** Manual reset (testing / admin override). */
    public void resetCap(Long campaignId, String userId) {
        String key = fcapKey(campaignId, userId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            jedis.del(key);
        } catch (Exception e) {
            log.warn("[FrequencyCap] resetCap error: {}", e.getMessage());
        }
    }

    private String fcapKey(Long campaignId, String userId) {
        return KEY_PREFIX + campaignId + ":" + userId;
    }
}
