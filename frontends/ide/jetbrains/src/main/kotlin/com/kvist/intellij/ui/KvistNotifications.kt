package com.kvist.intellij.ui

import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.notification.Notifications
import com.intellij.openapi.project.Project

object KvistNotifications {
    private const val GROUP_ID = "Kvist Notifications"

    fun error(project: Project?, content: String) = notify(project, content, NotificationType.ERROR)

    fun info(project: Project?, content: String) = notify(project, content, NotificationType.INFORMATION)

    private fun notify(project: Project?, content: String, type: NotificationType) {
        val notification = NotificationGroupManager.getInstance()
            .getNotificationGroup(GROUP_ID)
            .createNotification(content, type)

        if (project != null) {
            Notifications.Bus.notify(notification, project)
        }
        else {
            Notifications.Bus.notify(notification)
        }
    }
}
