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
 *
 * <p>TTL = configurable per-campaign window ({@code fcap.window.hours}).
 * Default: 24 hours (daily frequency cap).</p>
 *
 * <p>Fail-open: on Redis errors the impression is allowed.
 * This prevents Redis outages from blocking all bids.</p>
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
     * Check whether the user has NOT exceeded the frequency cap for this campaign.
     *
     * @param campaignId  Campaign ID
     * @param userId      User ID (buyer UID / cookie / device ID)
     * @param maxImpressions Maximum allowed impressions in the window
     * @return true if the user is within cap (bid is allowed), false if capped
     */
    public boolean isWithinCap(Long campaignId, String userId, int maxImpressions) {
        if (userId == null || userId.isBlank()) return true; // no user ID = no cap
        String key = fcapKey(campaignId, userId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            int count = val != null ? Integer.parseInt(val) : 0;
            return count < maxImpressions;
        } catch (Exception e) {
            log.warn("[FrequencyCap] isWithinCap error campaign={} user={}: {}",
                    campaignId, userId, e.getMessage());
            return true; // fail-open
        }
    }

    /**
     * Overload using default limit from configuration.
     */
    public boolean isWithinCap(Long campaignId, String userId) {
        return isWithinCap(campaignId, userId, defaultFcapLimit);
    }

    /**
     * Record an impression (called on win notice).
     *
     * <p>Atomically increments the counter and sets TTL on first call.
     * Uses INCR + conditional EXPIRE to avoid race conditions.</p>
     *
     * @param campaignId  Campaign ID
     * @param userId      User ID
     */
    public void recordImpression(Long campaignId, String userId) {
        if (userId == null || userId.isBlank()) return;
        String key = fcapKey(campaignId, userId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            long newCount = jedis.incr(key);
            // Set TTL only on first increment (avoids resetting window on every impression).
            if (newCount == 1) {
                jedis.expire(key, (long) fcapWindowHours * 3600);
            }
            log.debug("[FrequencyCap] campaign={} user={} impressions={}",
                    campaignId, userId, newCount);
        } catch (Exception e) {
            log.error("[FrequencyCap] recordImpression error campaign={} user={}: {}",
                    campaignId, userId, e.getMessage());
        }
    }

    /**
     * Get current impression count for a user/campaign pair.
     * Useful for debug endpoints and monitoring.
     */
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

    /**
     * Reset frequency cap for a user/campaign (for testing or manual overrides).
     */
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
