package com.kvist.intellij.chat

import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.intellij.ui.content.ContentFactory
import java.awt.Container
import java.lang.reflect.Proxy
import javax.swing.JButton
import javax.swing.JLabel

class KvistToolWindowFactoryTest : BasePlatformTestCase() {
    fun testMissingJcefClassesOfferTerminalInsteadOfFailingToolWindowInitialization() {
        // Reproduce the 2026.2 classloader failure even on the 2025.3 test platform,
        // where JCEF is still in core. Load the factory itself through this loader.
        val factoryName = KvistToolWindowFactory::class.java.name
        val parentLoader = KvistToolWindowFactory::class.java.classLoader
        val loader = object : ClassLoader(parentLoader) {
            override fun loadClass(name: String, resolve: Boolean): Class<*> {
                if (name.startsWith("com.intellij.ui.jcef.") || name.startsWith("org.cef.")) {
                    throw ClassNotFoundException(name)
                }
                if (name == factoryName || name.startsWith("$factoryName\$")) {
                    val clazz = findLoadedClass(name) ?: run {
                        val path = name.replace('.', '/') + ".class"
                        val bytes = parentLoader.getResourceAsStream(path)!!.use { it.readBytes() }
                        defineClass(name, bytes, 0, bytes.size)
                    }
                    if (resolve) resolveClass(clazz)
                    return clazz
                }
                return super.loadClass(name, resolve)
            }
        }
        val factory = loader.loadClass(factoryName).getDeclaredConstructor().newInstance() as ToolWindowFactory
        val contentManager = ContentFactory.getInstance().createContentManager(false, project)
        val toolWindow = Proxy.newProxyInstance(
            ToolWindow::class.java.classLoader,
            arrayOf(ToolWindow::class.java),
        ) { _, method, _ ->
            when (method.name) {
                "getContentManager" -> contentManager
                else -> error("Unexpected tool window call: ${method.name}")
            }
        } as ToolWindow

        factory.createToolWindowContent(project, toolWindow)

        assertEquals(1, contentManager.contentCount)
        val panel = contentManager.contents.single().component as Container
        val message = panel.components.filterIsInstance<JLabel>().single()
        assertTrue(message.text.contains("Enable Web Browser (JCEF)"))
        val buttons = panel.components.filterIsInstance<Container>().flatMap { it.components.toList() }
        assertEquals("Open Kvist in Terminal", buttons.filterIsInstance<JButton>().single().text)
    }
}
